// Small geometry helpers shared by the browser and the build scripts.
// Distances use a local equirectangular projection around the query point: accurate to well
// under 1% over the few-hundred-km scales used here.

const KM_PER_DEG_LAT = 110.57;
const kmPerDegLon = (lat) => 111.32 * Math.cos((lat * Math.PI) / 180);

/** Polygon / MultiPolygon geometry (or FeatureCollection of them) -> array of polygons (arrays of rings). */
export function polygonsOf(geo) {
  if (!geo) return [];
  if (geo.type === "FeatureCollection") return geo.features.flatMap((f) => polygonsOf(f.geometry));
  if (geo.type === "Feature") return polygonsOf(geo.geometry);
  if (geo.type === "Polygon") return [geo.coordinates];
  if (geo.type === "MultiPolygon") return geo.coordinates;
  return [];
}

function inRing([x, y], ring) {
  let c = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

/** Is the [lon, lat] point inside any of the polygons (respecting holes)? */
export function pointInPolygons(pt, polys) {
  for (const poly of polys) {
    if (!inRing(pt, poly[0])) continue;
    if (!poly.slice(1).some((hole) => inRing(pt, hole))) return true;
  }
  return false;
}

/** Shortest distance in km from a point to any polygon edge (ignores inside/outside). */
export function distanceToEdgesKm([lon, lat], polys) {
  const kx = kmPerDegLon(lat), ky = KM_PER_DEG_LAT;
  let best = Infinity;
  for (const poly of polys)
    for (const ring of poly)
      for (let i = 1; i < ring.length; i++) {
        const ax = (ring[i - 1][0] - lon) * kx, ay = (ring[i - 1][1] - lat) * ky;
        const bx = (ring[i][0] - lon) * kx, by = (ring[i][1] - lat) * ky;
        const dx = bx - ax, dy = by - ay;
        const len2 = dx * dx + dy * dy;
        const t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
        const px = ax + t * dx, py = ay + t * dy;
        const d = px * px + py * py;
        if (d < best) best = d;
      }
  return Math.sqrt(best);
}

/** Signed distance in km: > 0 outside the polygons (e.g. not yet occupied), < 0 inside. */
export function signedDistanceKm(pt, polys) {
  const d = distanceToEdgesKm(pt, polys);
  return pointInPolygons(pt, polys) ? -d : d;
}

/** km offsets -> degrees at a latitude. */
export const kmToDeg = (dxKm, dyKm, lat) => [dxKm / kmPerDegLon(lat), dyKm / KM_PER_DEG_LAT];

/** Outward unit normal (in km space) of a ring at vertex i, using its neighbours. */
export function vertexNormal(ring, i, polys) {
  const n = ring.length - 1; // closed ring: last == first
  const prev = ring[(i - 1 + n) % n], next = ring[(i + 1) % n], p = ring[i];
  const kx = kmPerDegLon(p[1]), ky = KM_PER_DEG_LAT;
  const tx = (next[0] - prev[0]) * kx, ty = (next[1] - prev[1]) * ky;
  const len = Math.hypot(tx, ty) || 1;
  let nx = ty / len, ny = -tx / len;
  // Make sure it points out of the polygon (probe 0.3 km along it).
  const [ox, oy] = kmToDeg(nx * 0.3, ny * 0.3, p[1]);
  if (pointInPolygons([p[0] + ox, p[1] + oy], polys)) { nx = -nx; ny = -ny; }
  return [nx, ny];
}
