// Builds marker icons: a coloured disc (by side) with a Lucide symbol (by type).
import * as lucide from "lucide-static";
import { SIDES, TYPES, trustBand, trustRingWidth } from "./event-config.js";

// Disc geometry in the 32-unit icon box. RING_INNER is the disc's outer edge (incl. its thin
// white separator stroke); the trust ring is drawn outside it. Shared with the map ring layer.
const DISC_R = 14.5;
const SEP_W = 1.5;
export const RING_INNER = DISC_R + SEP_W / 2; // 15.25 of 32

const innerSvg = (name) =>
  (lucide[name] ?? lucide.TriangleAlert)
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/^[\s\S]*?<svg[^>]*>/, "")
    .replace(/<\/svg>\s*$/, "");

/**
 * SVG markup for a marker. `size` is the rendered pixel size of the disc.
 * Pass `trust` (number 0..1, or null for unrated) to draw the trust ring around it, as on the map;
 * the SVG then grows by the ring width so the disc stays `size` px.
 */
export function iconSvg(side, type, size = 32, trust) {
  const s = SIDES[side] ?? SIDES.unknown;
  const t = TYPES[type] ?? TYPES.other;
  let pad = 0, ring = "";
  if (trust !== undefined) {
    const w = (trustRingWidth(trust) * 32) / size; // same on-screen px width as the map ring
    pad = w + 0.5;
    ring = `<circle cx="16" cy="16" r="${RING_INNER + w / 2}" fill="none" stroke="${trustBand(trust).color}" stroke-width="${w}"/>`;
  }
  const px = Math.round(size * (32 + 2 * pad) / 32);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${px}" height="${px}" viewBox="${-pad} ${-pad} ${32 + 2 * pad} ${32 + 2 * pad}">
  ${ring}
  <circle cx="16" cy="16" r="${DISC_R}" fill="${s.color}" stroke="#ffffff" stroke-width="${SEP_W}"/>
  <g transform="translate(7 7) scale(0.75)" fill="none" stroke="${s.fg}" stroke-width="2.2"
     stroke-linecap="round" stroke-linejoin="round">${innerSvg(t.icon)}</g>
</svg>`;
}

/** Symbol only (no disc), for the legend. */
export function symbolSvg(type, size = 16) {
  const t = TYPES[type] ?? TYPES.other;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24"
  fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${innerSvg(t.icon)}</svg>`;
}

export const iconKey = (side, type) => `ev-${side}-${type}`;

/** Rasterise every side x type combination once (2x for sharp rendering). */
export async function loadIconImages() {
  const images = new Map();
  const jobs = [];
  for (const side of Object.keys(SIDES)) {
    for (const type of Object.keys(TYPES)) {
      const img = new Image(64, 64);
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(iconSvg(side, type, 64));
      jobs.push(img.decode().then(() => images.set(iconKey(side, type), img)));
    }
  }
  await Promise.all(jobs);
  return images;
}
