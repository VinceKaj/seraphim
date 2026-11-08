// Generic overlay system: a list of toggleable overlays, grouped, rendered as a Layers panel.
//
// An overlay is a plain object:
//   {
//     id:        unique string (also the persistence key)
//     group:     id of a group in the `groups` list passed to the manager
//     label:     text in the panel
//     detail:    optional small text after the label (e.g. "as of 7 Nov", a count)
//     swatch:    CSS colour for the panel dot
//     defaultOn: initial state when the viewer has no saved choice
//     install(map):     optional; add sources/layers. Called after every style load
//                       (switching basemap theme wipes custom layers), so it must be idempotent.
//     apply(map, on):   optional; show/hide its layers.
//   }
// Overlays that only filter other content (e.g. trust bands) skip install/apply and are read
// through manager.state / manager.onChange().
//
// A group is { id, label, presets?: [{ label, on: [overlayIds] }] }.

const STORAGE_KEY = "overlay-state-v1";

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? {}; } catch { return {}; }
}
function save(state) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* private mode etc. */ }
}

export class OverlayManager {
  constructor(map, groups, overlays) {
    this.map = map;
    this.groups = groups;
    this.overlays = overlays;
    this.listeners = [];
    const saved = loadSaved();
    this.state = Object.fromEntries(overlays.map((o) => [o.id, saved[o.id] ?? o.defaultOn ?? true]));
    // Not map.isStyleLoaded(): that stays false while tiles are still loading.
    this.styleReady = false;
    map.on("style.load", () => { this.styleReady = true; this.install(); });
  }

  get(id) { return this.overlays.find((o) => o.id === id); }
  isOn(id) { return !!this.state[id]; }
  onChange(fn) { this.listeners.push(fn); }

  /** Add an overlay after construction (e.g. once its data has loaded). */
  add(overlay) {
    this.overlays.push(overlay);
    this.state[overlay.id] ??= loadSaved()[overlay.id] ?? overlay.defaultOn ?? true;
    // If the style is mid-reload this throws; the next style.load installs it instead.
    if (this.styleReady) try { this.installOne(overlay); } catch { /* installed on style.load */ }
    this.render();
  }

  set(changes) {
    Object.assign(this.state, changes);
    save(this.state);
    for (const o of this.overlays) if (o.id in changes) o.apply?.(this.map, this.state[o.id]);
    this.render();
    for (const fn of this.listeners) fn(this.state);
  }

  installOne(o) {
    o.install?.(this.map);
    o.apply?.(this.map, this.state[o.id]);
  }

  install() {
    for (const o of this.overlays) this.installOne(o);
  }

  /** Update an overlay's label/detail text (e.g. counts) without touching map state. */
  setDetail(id, detail) {
    const o = this.get(id);
    if (o && o.detail !== detail) { o.detail = detail; this.render(); }
  }

  mount(container) {
    this.container = container;
    container.addEventListener("change", (e) => {
      const id = e.target?.dataset?.overlay;
      if (id) this.set({ [id]: e.target.checked });
    });
    container.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-preset]");
      if (!btn) return;
      const [gid, idx] = btn.dataset.preset.split(":");
      const group = this.groups.find((g) => g.id === gid);
      const on = new Set(group.presets[Number(idx)].on);
      this.set(Object.fromEntries(this.overlays.filter((o) => o.group === gid).map((o) => [o.id, on.has(o.id)])));
    });
    this.render();
  }

  render() {
    if (!this.container) return;
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
    this.container.innerHTML = this.groups
      .map((g) => {
        const items = this.overlays.filter((o) => o.group === g.id);
        if (!items.length) return "";
        const presets = (g.presets ?? [])
          .map((p, i) => `<button type="button" data-preset="${esc(g.id)}:${i}">${esc(p.label)}</button>`)
          .join("");
        return `<fieldset>
          <legend>${esc(g.label)}${presets ? `<span class="presets">${presets}</span>` : ""}</legend>
          ${items.map((o) => `<label>
              <input type="checkbox" data-overlay="${esc(o.id)}" ${this.state[o.id] ? "checked" : ""}>
              <span class="swatch" style="background:${esc(o.swatch)}"></span>
              <span class="ov-label">${esc(o.label)}</span>
              ${o.detail ? `<span class="ov-detail">${esc(o.detail)}</span>` : ""}
            </label>`).join("")}
        </fieldset>`;
      })
      .join("");
  }
}

/** Index of the first symbol (label) layer, so area overlays can sit underneath place names. */
export function firstSymbolLayerId(map) {
  return map.getStyle().layers.find((l) => l.type === "symbol")?.id;
}

const EMPTY = { type: "FeatureCollection", features: [] };
const fmtDay = (d) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" });
const shift = (day, n) => { const d = new Date(day + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
export const FRONT_COLORS = { held: "#c62828", line: "#ff3b3b", gain: "#ff5a4f", loss: "#2f7df6" };
export const CHANGE_WINDOWS = [3, 7, 14, 30];

/**
 * Front line from DeepStateMap daily snapshots (/data/frontline/YYYY-MM-DD.geojson, listed in index.json).
 * Returns three overlays sharing one state, so each can be toggled in the Layers panel:
 *   held   - Russian-held territory on the selected day (red, with the front line)
 *   gains  - held now but not `windowDays` earlier: recent Russian advances (brighter red)
 *   losses - held `windowDays` earlier but not now: recent Ukrainian recaptures (blue)
 * `front.showDate(map, day)` loads the newest snapshot on or before `day`; `front.setWindow(map, n)` changes
 * how far back "recent" reaches. `onStats({ day, before, gainKm2, lossKm2 } | null)` reports each result.
 */
export function deepStateFront(dates, { onDetail, onStats, windowDays = 7 } = {}) {
  dates = [...dates].sort();
  const cache = new Map(); // day -> Promise<FeatureCollection>
  const diffs = new Map(); // "now|before" -> Promise<diff>
  const data = { held: EMPTY, gains: EMPTY, losses: EMPTY };
  const worker = new Worker(new URL("./front-diff.worker.js", import.meta.url), { type: "module" });
  const pending = new Map();
  let nextId = 0, requested = null, map = null;
  worker.onmessage = ({ data: r }) => { pending.get(r.id)?.(r); pending.delete(r.id); };

  const latestOnOrBefore = (day) => { let best = null; for (const d of dates) if (d <= day) best = d; else break; return best; };
  const load = (day) => {
    if (!cache.has(day))
      cache.set(day, fetch(`/data/frontline/${day}.geojson`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))));
    return cache.get(day).catch((e) => { cache.delete(day); throw e; });
  };
  // One diff runs at a time; a newer request replaces any that is still waiting (it resolves as superseded),
  // so fast playback never builds up a backlog: the worker always moves on to the newest day.
  let busy = false, waiting = null;
  const run = (job) => {
    busy = true;
    Promise.all([load(job.now), load(job.before)])
      .then(([a, b]) => new Promise((resolve) => {
        const id = ++nextId;
        pending.set(id, resolve);
        worker.postMessage({ id, now: a, before: b });
      }))
      .catch((e) => ({ error: String(e?.message ?? e) }))
      .then((r) => {
        if (r.error) diffs.delete(job.key);
        job.resolve(r);
        busy = false;
        if (waiting) { const next = waiting; waiting = null; run(next); }
      });
  };
  const diff = (now, before) => {
    const key = `${now}|${before}`;
    if (!diffs.has(key)) {
      diffs.set(key, new Promise((resolve) => {
        const job = { key, now, before, resolve };
        if (!busy) run(job);
        else {
          if (waiting) { diffs.delete(waiting.key); waiting.resolve({ superseded: true }); }
          waiting = job;
        }
      }));
      if (diffs.size > 400) diffs.delete(diffs.keys().next().value); // bounded cache
    }
    return diffs.get(key);
  };
  const setData = () => {
    if (!map) return;
    map.getSource("front-held")?.setData(data.held);
    map.getSource("front-gains")?.setData(data.gains);
    map.getSource("front-losses")?.setData(data.losses);
  };

  const front = {
    dates,
    get windowDays() { return windowDays; },
    async showDate(m, day) {
      map = m;
      requested = day;
      const snap = latestOnOrBefore(day);
      if (!snap) {
        Object.assign(data, { held: EMPTY, gains: EMPTY, losses: EMPTY });
        setData();
        onDetail?.(`no data before ${fmtDay(dates[0])}`);
        onStats?.(null);
        return;
      }
      const before = latestOnOrBefore(shift(snap, -windowDays));
      let held = EMPTY;
      try { held = await load(snap); } catch { /* keep empty */ }
      if (requested !== day) return; // superseded by a newer request
      data.held = held;
      onDetail?.(`DeepState · ${fmtDay(snap)}${snap !== day ? " (latest before)" : ""}`);
      if (!before || before === snap) {
        data.gains = data.losses = EMPTY;
        setData();
        onStats?.({ day: snap, before: null });
        return;
      }
      setData(); // show the territory right away; the changes follow
      const r = await diff(snap, before);
      if (requested !== day || r.superseded) return;
      if (r.error) { console.warn("front diff failed:", r.error); data.gains = data.losses = EMPTY; }
      else { data.gains = r.gains; data.losses = r.losses; }
      setData();
      onStats?.({ day: snap, before, gainKm2: r.gainKm2 ?? 0, lossKm2: r.lossKm2 ?? 0, error: r.error });
      // Warm the cache for the next day, so stepping forward is quick.
      const i = dates.indexOf(snap);
      if (i >= 0 && dates[i + 1]) load(dates[i + 1]).catch(() => {});
    },
    /** Start downloading a day's snapshot (e.g. the next frames of an animation). */
    prefetch(day) { const d = latestOnOrBefore(day); if (d) load(d).catch(() => {}); },
    setWindow(m, n) {
      windowDays = n;
      if (requested) front.showDate(m, requested);
    },
  };

  const layerSet = (id, layers) => ({
    install(m) {
      map = m;
      if (m.getSource(`front-${id}`)) return;
      m.addSource(`front-${id}`, {
        type: "geojson",
        data: data[id],
        ...(id === "held" ? { attribution: '<a href="https://deepstatemap.live" target="_blank">DeepStateMap</a>' } : {}),
      });
      const before = firstSymbolLayerId(m);
      for (const l of layers) m.addLayer({ ...l, source: `front-${id}` }, before);
    },
    apply(m, on) {
      for (const l of layers) if (m.getLayer(l.id)) m.setLayoutProperty(l.id, "visibility", on ? "visible" : "none");
    },
  });
  const width = (a, b, c) => ["interpolate", ["linear"], ["zoom"], 5, a, 10, b, 14, c];

  const overlays = [
    {
      id: "front-held", group: "territory", label: "Russian-occupied territory", swatch: FRONT_COLORS.held, defaultOn: true,
      ...layerSet("held", [
        { id: "front-held-fill", type: "fill", paint: { "fill-color": FRONT_COLORS.held, "fill-opacity": 0.32 } },
        { id: "front-held-line", type: "line", layout: { "line-join": "round" }, paint: { "line-color": FRONT_COLORS.line, "line-width": width(1.2, 2.5, 4) } },
      ]),
    },
    {
      id: "front-gains", group: "territory", label: "Recent Russian gains", swatch: FRONT_COLORS.gain, defaultOn: true,
      ...layerSet("gains", [
        { id: "front-gains-fill", type: "fill", paint: { "fill-color": FRONT_COLORS.gain, "fill-opacity": 0.7 } },
        { id: "front-gains-line", type: "line", layout: { "line-join": "round" }, paint: { "line-color": "#ff8a80", "line-width": width(0.6, 1.2, 2) } },
      ]),
    },
    {
      id: "front-losses", group: "territory", label: "Recent Ukrainian recaptures", swatch: FRONT_COLORS.loss, defaultOn: true,
      ...layerSet("losses", [
        { id: "front-losses-fill", type: "fill", paint: { "fill-color": FRONT_COLORS.loss, "fill-opacity": 0.65 } },
        { id: "front-losses-line", type: "line", layout: { "line-join": "round" }, paint: { "line-color": "#8ab6ff", "line-width": width(0.6, 1.2, 2) } },
      ]),
    },
  ];
  return { front, overlays };
}
