// Time slider mode: animates the front line through every DeepState snapshot, with a timeline of weekly
// territorial change (Russian gains up, Ukrainian recaptures down) that can be scrubbed, and a list of the
// biggest changes.
//
// "Focus on big changes" paces the animation by how much changed hands each day: quiet days flash past,
// big ones dwell (up to ~1 s at 1×). "Follow the action" moves the camera to the largest changed piece
// when it's off screen. Data: /data/frontline/changes.json (scripts/build-changes.mjs).
import { FRONT_COLORS } from "./overlays.js";

const fmtDay = (d) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const fmtMonth = (d) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
const fmtKm2 = (x) => (x >= 100 ? Math.round(x).toLocaleString() : x >= 10 ? x.toFixed(0) : x.toFixed(1));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const daysBetween = (a, b) => (Date.parse(b) - Date.parse(a)) / 86400000;
const SPEEDS = [0.5, 1, 2, 4];
const BIG_DAY_KM2 = 300;  // a day with this much change gets the full dwell
const FOLLOW_MIN_KM2 = 15; // smaller changes don't move the camera
const PERIODS = 8;         // entries in "Biggest changes"
const PER_YEAR = 2;        // ...at most this many per year, so 2022's huge swings don't crowd out later years
const MIN_PERIOD_KM2 = 100;

/** Weekly buckets (7 days from the first snapshot) of gains and recaptures. */
function weekly(days) {
  const t0 = Date.parse(days[0].d);
  const weeks = [];
  for (const r of days) {
    const w = Math.floor((Date.parse(r.d) - t0) / (7 * 86400000));
    weeks[w] ??= { start: new Date(t0 + w * 7 * 86400000).toISOString().slice(0, 10), g: 0, l: 0, first: r.d, last: r.d, f: null, fa: 0 };
    const b = weeks[w];
    b.g += r.g; b.l += r.l; b.last = r.d;
    if (r.f && r.g + r.l > b.fa) { b.f = r.f; b.fa = r.g + r.l; }
  }
  for (let i = 0; i < weeks.length; i++) weeks[i] ??= { start: null, g: 0, l: 0, first: null, last: null, f: null, fa: 0 };
  return weeks;
}

/** The biggest change periods: peak weeks, extended over neighbours with at least 30% of the peak. */
function bigPeriods(weeks) {
  const score = (w) => w.g + w.l;
  const used = new Set(), out = [], perYear = new Map();
  for (const i of weeks.map((_, i) => i).sort((a, b) => score(weeks[b]) - score(weeks[a]))) {
    if (out.length >= PERIODS || used.has(i) || !weeks[i].first || score(weeks[i]) < MIN_PERIOD_KM2 / 4) continue;
    const year = weeks[i].first.slice(0, 4);
    if ((perYear.get(year) ?? 0) >= PER_YEAR) continue;
    const peak = score(weeks[i]);
    let a = i, b = i;
    while (a > 0 && !used.has(a - 1) && weeks[a - 1].first && score(weeks[a - 1]) >= 0.3 * peak) a--;
    while (b < weeks.length - 1 && !used.has(b + 1) && weeks[b + 1].first && score(weeks[b + 1]) >= 0.3 * peak) b++;
    for (let k = a - 1; k <= b + 1; k++) used.add(k); // keep periods apart
    const span = weeks.slice(a, b + 1);
    const g = span.reduce((s, w) => s + w.g, 0), l = span.reduce((s, w) => s + w.l, 0);
    const focus = span.reduce((best, w) => (w.fa > (best?.fa ?? 0) ? w : best), null);
    if (g + l < MIN_PERIOD_KM2) continue;
    perYear.set(year, (perYear.get(year) ?? 0) + 1);
    out.push({ from: span[0].first, to: span.at(-1).last, g, l, f: focus?.f ?? null, a, b });
  }
  return out.sort((x, y) => x.from.localeCompare(y.from));
}

export class TimeSlider {
  /**
   * @param {HTMLElement} panel
   * @param {{ days: object[], map: maplibregl.Map, onDay: (day: string) => void, prefetch?: (day: string) => void, onExit?: (day: string) => void }} opts
   */
  constructor(panel, { days, map, onDay, prefetch, onExit }) {
    Object.assign(this, { panel, days, map, onDay, prefetch, onExit });
    this.dates = days.map((r) => r.d);
    this.byDay = new Map(days.map((r) => [r.d, r]));
    this.weeks = weekly(days);
    this.periods = bigPeriods(this.weeks);
    this.i = this.dates.length - 1;
    this.speed = 1;
    this.focus = true;
    this.follow = true;
    this.timer = null;
    this.stopAt = null;
    this.render();
    this.bind();
  }

  get day() { return this.dates[this.i]; }
  get isOpen() { return !this.panel.hidden; }

  open(day) {
    this.panel.hidden = false;
    document.body.classList.add("timeslider");
    this.go(this.indexOf(day));
  }

  close() {
    this.pause();
    this.panel.hidden = true;
    document.body.classList.remove("timeslider");
    this.onExit?.(this.day);
  }

  indexOf(day) {
    if (!day || day === "all") return this.dates.length - 1;
    let k = 0;
    for (let j = 0; j < this.dates.length; j++) if (this.dates[j] <= day) k = j; else break;
    return k;
  }

  /** Show snapshot index i (clamped). */
  go(i, { follow = false } = {}) {
    this.i = Math.max(0, Math.min(this.dates.length - 1, i));
    const r = this.byDay.get(this.day);
    this.onDay(this.day);
    if (follow) this.followTo(r);
    for (let k = 1; k <= 4; k++) if (this.dates[this.i + k]) this.prefetch?.(this.dates[this.i + k]);
    this.update();
  }

  /** Delay before the next frame, by how much changed on this day. */
  delay(r) {
    const base = this.focus ? 45 + 950 * Math.min(1, Math.sqrt(((r?.g ?? 0) + (r?.l ?? 0)) / BIG_DAY_KM2)) : 250;
    return base / this.speed;
  }

  followTo(r) {
    if (!this.follow || !r?.f || r.g + r.l < FOLLOW_MIN_KM2) return;
    const [w, s, e, n] = r.f, center = [(w + e) / 2, (s + n) / 2];
    const b = this.map.getBounds(), z = this.map.getZoom();
    // Only move if the change is outside the middle 70% of the view, or we're zoomed far out/in.
    const dx = (b.getEast() - b.getWest()) * 0.15, dy = (b.getNorth() - b.getSouth()) * 0.15;
    const inside = center[0] > b.getWest() + dx && center[0] < b.getEast() - dx && center[1] > b.getSouth() + dy && center[1] < b.getNorth() - dy;
    const zoom = Math.min(8.5, Math.max(6.5, z));
    if (inside && zoom === z) return;
    this.map.easeTo({ center, zoom, duration: Math.min(900, Math.max(300, this.delay(r))) });
  }

  play() {
    if (this.timer) return;
    if (this.i >= this.dates.length - 1) this.go(0);
    this.playing = true;
    const tick = () => {
      if (this.stopAt && this.day >= this.stopAt) { this.stopAt = null; return this.pause(); }
      if (this.i >= this.dates.length - 1) return this.pause();
      this.go(this.i + 1, { follow: true });
      this.timer = setTimeout(tick, this.delay(this.byDay.get(this.day)));
    };
    this.timer = setTimeout(tick, 150);
    this.update();
  }

  pause() {
    clearTimeout(this.timer);
    this.timer = null;
    this.playing = false;
    this.update();
  }

  /** Jump to a big-change period and play through it. */
  playPeriod(p) {
    this.pause();
    const start = this.indexOf(this.dates[Math.max(0, this.indexOf(p.from) - 3)]);
    this.go(start);
    if (p.f) {
      const [w, s, e, n] = p.f;
      this.map.easeTo({ center: [(w + e) / 2, (s + n) / 2], zoom: Math.min(8.5, Math.max(6.5, this.map.getZoom())), duration: 800 });
    }
    this.stopAt = this.dates[Math.min(this.dates.length - 1, this.indexOf(p.to) + 3)];
    setTimeout(() => this.play(), 900);
  }

  render() {
    const W = 1000, H = 100, mid = H / 2;
    const maxV = Math.max(1, ...this.weeks.map((w) => Math.max(w.g, w.l)));
    // Log scale offset by 10 km²: 2022's ~10,000 km² weeks and today's ~100 km² weeks both stay readable,
    // while a few km² of DeepState redrawing stays near zero.
    const sc = (v) => (Math.log1p(v / 10) / Math.log1p(maxV / 10)) * (mid - 2);
    const bw = W / this.weeks.length;
    const x = (wi) => wi * bw;
    const bars = this.weeks.map((w, wi) => [
      w.g > 0 ? `<rect x="${x(wi)}" y="${mid - sc(w.g)}" width="${Math.max(0.6, bw - 0.6)}" height="${sc(w.g)}" fill="${FRONT_COLORS.gain}"/>` : "",
      w.l > 0 ? `<rect x="${x(wi)}" y="${mid}" width="${Math.max(0.6, bw - 0.6)}" height="${sc(w.l)}" fill="${FRONT_COLORS.loss}"/>` : "",
    ].join("")).join("");
    const bands = this.periods.map((p) => `<rect class="ts-band" x="${x(p.a)}" y="0" width="${(p.b - p.a + 1) * bw}" height="${H}"/>`).join("");
    // Year ticks
    const t0 = Date.parse(this.dates[0]), span = daysBetween(this.dates[0], this.dates.at(-1)) || 1;
    const years = [];
    for (let y = Number(this.dates[0].slice(0, 4)) + 1; y <= Number(this.dates.at(-1).slice(0, 4)); y++) years.push(y);
    const xDay = (d) => (daysBetween(this.dates[0], d) / span) * 100;
    this.xDay = xDay;
    this.panel.innerHTML = `
      <div class="ts-row">
        <button type="button" class="ts-play" title="Play / pause (space)">Play</button>
        <label>Speed <select class="ts-speed">${SPEEDS.map((s) => `<option value="${s}" ${s === this.speed ? "selected" : ""}>${s}×</option>`).join("")}</select></label>
        <label title="Slow down where a lot of territory changed hands, skip quickly through quiet periods"><input type="checkbox" class="ts-focus" ${this.focus ? "checked" : ""}> Focus on big changes</label>
        <label title="Move the map to the largest change when it's off screen"><input type="checkbox" class="ts-follow" ${this.follow ? "checked" : ""}> Follow the action</label>
        <span class="ts-now"></span>
        <button type="button" class="ts-exit" title="Leave time slider mode">Exit</button>
      </div>
      <div class="ts-chart">
        <div class="ts-axis"><span class="ts-gain">▲ Russian gains</span><span class="ts-loss">▼ Ukrainian recaptures</span><span class="ts-muted">km² per week, log scale</span></div>
        <div class="ts-plot">
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Weekly territory changed hands">
            ${bands}<line x1="0" x2="${W}" y1="${mid}" y2="${mid}" class="ts-zero"/>${bars}
          </svg>
          <div class="ts-cursor"></div>
          <div class="ts-hover" hidden></div>
          ${years.map((y) => `<span class="ts-year" style="left:${xDay(`${y}-01-01`)}%">${y}</span>`).join("")}
        </div>
        <input type="range" class="ts-range" min="0" max="${this.dates.length - 1}" step="1" value="${this.i}" aria-label="Date">
      </div>
      <div class="ts-periods"><span class="ts-muted">Biggest changes:</span>
        ${this.periods.map((p, k) => {
          const ru = p.g >= p.l;
          return `<button type="button" class="ts-period" data-period="${k}" style="--c:${ru ? FRONT_COLORS.gain : FRONT_COLORS.loss}"
            title="${esc(fmtDay(p.from))} – ${esc(fmtDay(p.to))}: +${fmtKm2(p.g)} km² Russian, ${fmtKm2(p.l)} km² recaptured. Click to play through it.">
            ${esc(fmtMonth(p.from))} · ${fmtKm2(ru ? p.g : p.l)} km² ${ru ? "taken by Russia" : "recaptured"}</button>`;
        }).join("")}
      </div>`;
    this.el = {
      play: this.panel.querySelector(".ts-play"), now: this.panel.querySelector(".ts-now"),
      range: this.panel.querySelector(".ts-range"), cursor: this.panel.querySelector(".ts-cursor"),
      plot: this.panel.querySelector(".ts-plot"), hover: this.panel.querySelector(".ts-hover"),
    };
    this.update();
  }

  update() {
    if (!this.el) return;
    const r = this.byDay.get(this.day);
    this.el.play.textContent = this.playing ? "Pause" : "Play";
    this.el.play.classList.toggle("active", !!this.playing);
    this.el.range.value = this.i;
    this.el.cursor.style.left = `${this.xDay(this.day)}%`;
    this.el.now.innerHTML = r ? `<b>${esc(fmtDay(r.d))}</b>
      <span class="ts-gain">+${fmtKm2(r.g)}</span> / <span class="ts-loss">${fmtKm2(r.l)}</span> km² since ${esc(r.p ? fmtDay(r.p) : "–")}
      · <span class="ts-muted">${Math.round(r.area).toLocaleString()} km² held</span>` : "";
  }

  /** Snapshot index nearest to an x position (0..1) on the timeline. */
  indexAtFrac(f) {
    const t = Date.parse(this.dates[0]) + f * (Date.parse(this.dates.at(-1)) - Date.parse(this.dates[0]));
    return this.indexOf(new Date(t).toISOString().slice(0, 10));
  }

  bind() {
    const p = this.panel;
    p.addEventListener("click", (e) => {
      if (e.target.closest(".ts-play")) this.playing ? this.pause() : this.play();
      else if (e.target.closest(".ts-exit")) this.close();
      else {
        const b = e.target.closest(".ts-period");
        if (b) this.playPeriod(this.periods[Number(b.dataset.period)]);
      }
    });
    p.addEventListener("change", (e) => {
      if (e.target.classList.contains("ts-speed")) this.speed = Number(e.target.value);
      if (e.target.classList.contains("ts-focus")) this.focus = e.target.checked;
      if (e.target.classList.contains("ts-follow")) this.follow = e.target.checked;
    });
    p.addEventListener("input", (e) => {
      if (!e.target.classList.contains("ts-range")) return;
      const i = Number(e.target.value); // read first: pause() redraws the controls
      this.pause();
      this.go(i);
    });
    // Scrub / hover on the chart.
    let dragging = false;
    const frac = (e) => { const b = this.el.plot.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - b.left) / b.width)); };
    p.addEventListener("pointerdown", (e) => {
      if (!e.target.closest(".ts-plot")) return;
      dragging = true;
      e.target.setPointerCapture?.(e.pointerId);
      const i = this.indexAtFrac(frac(e));
      this.pause();
      this.go(i);
    });
    p.addEventListener("pointermove", (e) => {
      if (!e.target.closest(".ts-plot")) { this.el.hover.hidden = true; return; }
      const f = frac(e);
      if (dragging) this.go(this.indexAtFrac(f));
      const w = this.weeks[Math.min(this.weeks.length - 1, Math.floor(f * this.weeks.length))];
      if (!w?.first) { this.el.hover.hidden = true; return; }
      this.el.hover.hidden = false;
      this.el.hover.style.left = `${f * 100}%`;
      this.el.hover.innerHTML = `<b>${esc(fmtDay(w.first))} – ${esc(fmtDay(w.last))}</b><br>
        <span class="ts-gain">+${fmtKm2(w.g)} km² Russian</span><br><span class="ts-loss">${fmtKm2(w.l)} km² recaptured</span>`;
    });
    p.addEventListener("pointerup", () => { dragging = false; });
    p.addEventListener("pointerleave", () => { this.el.hover.hidden = true; });
    document.addEventListener("keydown", (e) => {
      if (!this.isOpen || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.target.closest?.("input:not([type=range]), textarea, select, .maplibregl-canvas-container")) return;
      if (e.key === " " && !e.target.closest?.("button")) { this.playing ? this.pause() : this.play(); e.preventDefault(); }
      if (e.key === "ArrowLeft" && e.target.type !== "range") { this.pause(); this.go(this.i - 1); e.preventDefault(); }
      if (e.key === "ArrowRight" && e.target.type !== "range") { this.pause(); this.go(this.i + 1); e.preventDefault(); }
      if (e.key === "Escape") this.close();
    });
  }
}
