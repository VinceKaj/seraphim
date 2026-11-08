// Date selector bar: ◀ [date] ▶  [Play]  [All dates]. Play steps forward one day at a time (from 30 days back if
// already on the last day). Clicking the date opens a calendar: month/year menus to jump anywhere, then pick a
// day; days outside [min, max] are greyed out. Days are UTC "YYYY-MM-DD" strings; "all" = no date filter.

export const ALL = "all";

export const shiftDay = (day, n) => {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

const MONTHS = Array.from({ length: 12 }, (_, m) =>
  new Date(Date.UTC(2000, m, 1)).toLocaleDateString(undefined, { month: "long", timeZone: "UTC" }));
const WEEKDAYS = Array.from({ length: 7 }, (_, i) => // Monday first
  new Date(Date.UTC(2024, 0, 1 + i)).toLocaleDateString(undefined, { weekday: "narrow", timeZone: "UTC" }));
const pad = (n) => String(n).padStart(2, "0");
const fmtShort = (day) =>
  new Date(day + "T00:00:00Z").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

const fmtLong = (day) =>
  new Date(day + "T00:00:00Z").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export class DateBar {
  /**
   * @param {HTMLElement} el      container
   * @param {{min: string, max: string, value: string, onChange: (day: string) => void, stepDays?: number, playMs?: number}} opts
   */
  constructor(el, { min, max, value, onChange, stepDays = 1, playMs = 700 }) {
    this.min = min;
    this.max = max;
    this.stepDays = stepDays;
    this.onChange = onChange;
    this.playMs = playMs;
    this.timer = null;
    this.lastDay = value === ALL ? max : value; // where the arrows resume from after "All dates"
    el.innerHTML = `
      <button type="button" class="db-prev" title="Previous day (←)" aria-label="Previous day">◀</button>
      <button type="button" class="db-date" aria-haspopup="dialog" aria-expanded="false" title="Choose a date">
        <span class="db-label"></span> <span class="db-caret">▾</span>
      </button>
      <button type="button" class="db-next" title="Next day (→)" aria-label="Next day">▶</button>
      <button type="button" class="db-play" title="Play forward day by day (space)" aria-label="Play">Play</button>
      <button type="button" class="db-all" title="Show events from every date">All dates</button>
      <div class="db-cal" role="dialog" aria-label="Choose a date" hidden></div>`;
    this.prevBtn = el.querySelector(".db-prev");
    this.nextBtn = el.querySelector(".db-next");
    this.allBtn = el.querySelector(".db-all");
    this.playBtn = el.querySelector(".db-play");
    this.dateBtn = el.querySelector(".db-date");
    this.label = el.querySelector(".db-label");
    this.cal = el.querySelector(".db-cal");
    this.view = null; // { y, m } of the month shown in the calendar

    this.prevBtn.onclick = () => { this.stop(); this.step(-this.stepDays); };
    this.nextBtn.onclick = () => { this.stop(); this.step(this.stepDays); };
    this.allBtn.onclick = () => { this.stop(); this.set(this.value === ALL ? this.lastDay : ALL); };
    this.playBtn.onclick = () => (this.timer ? this.stop() : this.play());
    this.dateBtn.onclick = () => (this.cal.hidden ? this.openCalendar() : this.closeCalendar());
    this.cal.addEventListener("change", (e) => {
      if (e.target.dataset.cal === "month") this.view.m = Number(e.target.value);
      else if (e.target.dataset.cal === "year") this.view.y = Number(e.target.value);
      else return;
      this.clampView();
      this.renderCalendar();
    });
    this.cal.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      if (b.dataset.day) { this.stop(); this.closeCalendar(); this.set(b.dataset.day); }
      else if (b.dataset.nav) { this.view.m += Number(b.dataset.nav); this.normView(); this.clampView(); this.renderCalendar(); }
      else if (b.dataset.jump) { this.stop(); this.closeCalendar(); this.set(b.dataset.jump === "first" ? this.min : this.max); }
    });
    // Close on a click elsewhere or Escape.
    document.addEventListener("mousedown", (e) => { if (!this.cal.hidden && !el.contains(e.target)) this.closeCalendar(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !this.cal.hidden) { this.closeCalendar(); this.dateBtn.focus(); } });
    // Arrow keys step days when focus isn't in a text field or on the map (the map uses arrows to pan).
    document.addEventListener("keydown", (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (el.offsetParent === null) return; // bar hidden (e.g. time slider mode)
      if (e.target.closest?.("input, textarea, select, .maplibregl-canvas-container")) return;
      if (!this.cal.hidden) return; // the calendar is open
      if (e.key === "ArrowLeft") { this.stop(); this.step(-this.stepDays); e.preventDefault(); }
      if (e.key === "ArrowRight") { this.stop(); this.step(this.stepDays); e.preventDefault(); }
      if (e.key === " " && !e.target.closest?.("button")) { this.timer ? this.stop() : this.play(); e.preventDefault(); }
    });
    this.set(value, { silent: true });
  }

  step(n) {
    const from = this.value === ALL ? this.lastDay : this.value;
    const next = shiftDay(from, n);
    // Clamp to the range so a 7-day step still reaches the first/last day.
    if (from !== (n < 0 ? this.min : this.max)) this.set(next < this.min ? this.min : next > this.max ? this.max : next);
  }

  play() {
    if (this.value === ALL || this.value >= this.max) {
      const start = shiftDay(this.max, -30);
      this.set(start < this.min ? this.min : start);
    }
    this.playBtn.textContent = "Pause";
    this.playBtn.classList.add("active");
    this.playBtn.title = "Pause (space)";
    this.timer = setInterval(() => {
      if (this.value >= this.max) return this.stop();
      this.step(this.stepDays);
    }, this.playMs);
  }

  stop() {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
    this.playBtn.textContent = "Play";
    this.playBtn.classList.remove("active");
    this.playBtn.title = "Play forward day by day (space)";
  }

  openCalendar() {
    const day = this.value === ALL ? this.lastDay : this.value;
    this.view = { y: Number(day.slice(0, 4)), m: Number(day.slice(5, 7)) - 1 };
    this.renderCalendar();
    this.cal.hidden = false;
    this.dateBtn.setAttribute("aria-expanded", "true");
    this.cal.querySelector(".sel, .today, [data-day]:not(:disabled)")?.focus();
  }

  closeCalendar() {
    this.cal.hidden = true;
    this.dateBtn.setAttribute("aria-expanded", "false");
  }

  normView() {
    const v = this.view;
    v.y += Math.floor(v.m / 12);
    v.m = ((v.m % 12) + 12) % 12;
  }

  /** Keep the shown month inside the data range. */
  clampView() {
    const v = this.view, key = `${v.y}-${pad(v.m + 1)}`;
    const lo = this.min.slice(0, 7), hi = this.max.slice(0, 7);
    const k = key < lo ? lo : key > hi ? hi : key;
    v.y = Number(k.slice(0, 4));
    v.m = Number(k.slice(5, 7)) - 1;
  }

  renderCalendar() {
    const { y, m } = this.view;
    const minY = Number(this.min.slice(0, 4)), maxY = Number(this.max.slice(0, 4));
    const monthKey = (mm) => `${y}-${pad(mm + 1)}`;
    const monthOpts = MONTHS.map((name, i) => {
      const out = monthKey(i) < this.min.slice(0, 7) || monthKey(i) > this.max.slice(0, 7);
      return `<option value="${i}" ${i === m ? "selected" : ""} ${out ? "disabled" : ""}>${name}</option>`;
    }).join("");
    const yearOpts = Array.from({ length: maxY - minY + 1 }, (_, i) => minY + i)
      .map((yy) => `<option value="${yy}" ${yy === y ? "selected" : ""}>${yy}</option>`).join("");
    const first = new Date(Date.UTC(y, m, 1));
    const lead = (first.getUTCDay() + 6) % 7; // Monday = 0
    const daysIn = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const sel = this.value === ALL ? null : this.value;
    let cells = "";
    for (let i = 0; i < lead; i++) cells += `<span></span>`;
    for (let d = 1; d <= daysIn; d++) {
      const day = `${y}-${pad(m + 1)}-${pad(d)}`;
      const out = day < this.min || day > this.max;
      const cls = [day === sel ? "sel" : "", day === this.max ? "today" : ""].filter(Boolean).join(" ");
      cells += `<button type="button" data-day="${day}" class="${cls}" ${out ? "disabled" : ""} title="${fmtLong(day)}">${d}</button>`;
    }
    const atStart = monthKey(m) <= this.min.slice(0, 7), atEnd = monthKey(m) >= this.max.slice(0, 7);
    this.cal.innerHTML = `
      <div class="cal-head">
        <button type="button" data-nav="-1" ${atStart ? "disabled" : ""} aria-label="Previous month">‹</button>
        <select data-cal="month" aria-label="Month">${monthOpts}</select>
        <select data-cal="year" aria-label="Year">${yearOpts}</select>
        <button type="button" data-nav="1" ${atEnd ? "disabled" : ""} aria-label="Next month">›</button>
      </div>
      <div class="cal-grid">${WEEKDAYS.map((w) => `<span class="wd">${w}</span>`).join("")}${cells}</div>
      <div class="cal-foot">
        <button type="button" data-jump="first">First (${fmtShort(this.min)})</button>
        <button type="button" data-jump="latest">Latest (${fmtShort(this.max)})</button>
      </div>`;
  }

  set(day, { silent = false } = {}) {
    if (day !== ALL) day = day < this.min ? this.min : day > this.max ? this.max : day;
    this.value = day;
    if (day !== ALL) this.lastDay = day;
    const all = day === ALL;
    this.label.textContent = all ? "All dates" : fmtLong(day);
    this.prevBtn.disabled = !all && day <= this.min;
    this.nextBtn.disabled = !all && day >= this.max;
    this.allBtn.classList.toggle("active", all);
    if (!silent) this.onChange(day);
  }
}
