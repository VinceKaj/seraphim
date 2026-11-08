// Shared by the browser (src/main.js) and the build script (scripts/build-events.mjs).
// Keys are what you write in an event .md file: `side: russia`, `type: attack`.

// side -> marker colour. `fg` is the symbol colour drawn on top (dark on yellow for contrast).
export const SIDES = {
  russia:       { label: "Russia",            color: "#d32f2f", fg: "#ffffff" },
  ukraine:      { label: "Ukraine",           color: "#1e6fd9", fg: "#ffffff" },
  "third-party": { label: "Third party",      color: "#f2c200", fg: "#1a1a1a" },
  humanitarian: { label: "Humanitarian",      color: "#8e44ad", fg: "#ffffff" },
  unknown:      { label: "Unknown",           color: "#7a7a7a", fg: "#ffffff" },
};

// type -> symbol. `icon` is a Lucide icon export name (https://lucide.dev/icons).
export const TYPES = {
  attack:     { label: "Attack",          icon: "Skull" },
  airstrike:  { label: "Airstrike",       icon: "Plane" },
  missile:    { label: "Missile",         icon: "Rocket" },
  drone:      { label: "Drone",           icon: "Drone" },
  shelling:   { label: "Shelling",        icon: "Bomb" },
  fire:       { label: "Fire / explosion", icon: "Flame" },
  movement:   { label: "Troop movement",  icon: "Footprints" },
  capture:    { label: "Territory taken", icon: "Flag" },
  naval:      { label: "Naval",           icon: "Anchor" },
  aid:        { label: "Aid delivery",    icon: "HeartHandshake" },
  evacuation: { label: "Evacuation",      icon: "Truck" },
  statement:  { label: "Statement",       icon: "Megaphone" },
  other:      { label: "Other",           icon: "TriangleAlert" },
};

// Group kinds (groups/<id>.md `kind:`). `area: true` draws an outline around the group's events on the
// map (useful for localised ground operations; a group file can override it with `area: yes|no`).
export const GROUP_KINDS = {
  offensive:          { label: "Offensive",          area: true },
  "counter-offensive": { label: "Counter-offensive", area: true },
  defensive:          { label: "Defensive operation", area: true },
  "strike-campaign":  { label: "Strike campaign",    area: false },
  "naval-campaign":   { label: "Naval campaign",     area: false },
  humanitarian:       { label: "Humanitarian operation", area: false },
  other:              { label: "Group",              area: false },
};

// trust-factor (0 = highly untrustworthy, 1 = very trustworthy) -> filter bands shown as
// toggleable overlays. A band matches min <= trust < max (the top band includes 1).
// Events without a trust-factor fall in "unrated". Edit thresholds/labels freely.
export const TRUST_BANDS = [
  { id: "trusted",   label: "Trusted",   min: 0.7, max: Infinity, color: "#43a047" },
  { id: "uncertain", label: "Uncertain", min: 0.4, max: 0.7,      color: "#fbc02d" },
  { id: "untrusted", label: "Untrusted", min: 0,   max: 0.4,      color: "#e53935" },
  { id: "unrated",   label: "Unrated",   unrated: true,           color: "#9e9e9e" },
];

/** Band for a trust value (number 0..1, or null/undefined for unrated). */
export function trustBand(trust) {
  if (trust == null) return TRUST_BANDS.find((b) => b.unrated);
  return TRUST_BANDS.find((b) => !b.unrated && trust >= b.min && trust < b.max);
}

// Trust ring around each marker: colour = band colour, width (px) grows with trust.
export const TRUST_RING = { minWidth: 1, maxWidth: 5, unratedWidth: 1.5 };

/** Ring width in px for a trust value (null = unrated). Mirrored as a map expression in main.js. */
export function trustRingWidth(trust) {
  const { minWidth, maxWidth, unratedWidth } = TRUST_RING;
  return trust == null ? unratedWidth : minWidth + (maxWidth - minWidth) * trust;
}

/** Human-readable range for a band, e.g. "≥ 0.7", "0.4–0.7", "< 0.4". */
export function bandRange(b) {
  if (b.unrated) return "no trust-factor";
  if (b.max === Infinity) return `≥ ${b.min}`;
  if (b.min <= 0) return `< ${b.max}`;
  return `${b.min}–${b.max}`;
}

// Accepted alternative spellings in .md files -> canonical key.
export const SIDE_ALIASES = {
  ru: "russia", russian: "russia", russians: "russia",
  ua: "ukraine", ukrainian: "ukraine", ukrainians: "ukraine",
  "third party": "third-party", thirdparty: "third-party", third: "third-party",
  ngo: "humanitarian", aid: "humanitarian",
};
