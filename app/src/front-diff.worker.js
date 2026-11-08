// Web worker: territory that changed hands between two DeepState snapshots.
//   in:  { id, now, before }  (FeatureCollections of Russian-held territory)
//   out: { id, gains, losses, gainKm2, lossKm2 }
// gains  = held now but not before (recent Russian advances)
// losses = held before but not now (recent Ukrainian recaptures)
// Pieces under MIN_KM2 are dropped: they come from DeepState redrawing the line, not from movement.
import polygonClipping from "polygon-clipping";

const MIN_KM2 = 0.1;

const toMulti = (fc) =>
  fc.features.flatMap((f) => (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates));

/** Approximate area (km²) of a polygon [outer, ...holes] in lon/lat, fine for areas of this size. */
function km2(poly) {
  let total = 0;
  poly.forEach((ring, k) => {
    const coslat = Math.cos((ring[0][1] * Math.PI) / 180);
    let a = 0;
    for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    a = (Math.abs(a) / 2) * 111.32 * 110.57 * coslat;
    total += k ? -a : a;
  });
  return total;
}

function toFeatures(multi) {
  const features = [];
  let sum = 0;
  for (const poly of multi) {
    const area = km2(poly);
    if (area < MIN_KM2) continue;
    sum += area;
    features.push({ type: "Feature", properties: { km2: Math.round(area * 100) / 100 }, geometry: { type: "Polygon", coordinates: poly } });
  }
  return [{ type: "FeatureCollection", features }, sum];
}

self.onmessage = ({ data: { id, now, before } }) => {
  try {
    const a = toMulti(before), b = toMulti(now);
    const [gains, gainKm2] = toFeatures(polygonClipping.difference(b, a));
    const [losses, lossKm2] = toFeatures(polygonClipping.difference(a, b));
    self.postMessage({ id, gains, losses, gainKm2, lossKm2 });
  } catch (e) {
    self.postMessage({ id, error: String(e?.message ?? e) });
  }
};
