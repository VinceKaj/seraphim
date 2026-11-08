// Seraphim layers on top of Kacper's map:
//  1. "Building damage (SIMULATED)": small squares, shown for the week of the selected day
//     (/data/damage.geojson from trust/export_extras.py; placeholder for Huge's satellite pipeline).
//  2. "Polymarket vs Seraphim": open territorial markets on the map + a panel with open markets and the
//     walk-forward track record on resolved markets (/polymarket.json).
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const pct = (x) => `${Math.round(x * 100)}%`;
const weekOf = (day) => {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
};

export function seraphimLayers(map, ALL) {
  let day = ALL;
  let pm = null;
  let damageWeeks = [];
  const panel = document.createElement("div");
  panel.id = "pm-panel";
  panel.hidden = true;
  document.body.appendChild(panel);
  const style = document.createElement("style");
  style.textContent = `
  #pm-panel{position:fixed;left:50px;bottom:34px;z-index:5;width:min(470px,calc(100vw - 70px));max-height:58vh;overflow:auto;
    background:#16181dee;color:#e8e8ea;border:1px solid #333;border-radius:10px;font:13px/1.35 system-ui,sans-serif;padding:10px 12px;box-shadow:0 6px 24px #0007}
  #pm-panel h3{margin:2px 0 6px;font-size:14px;cursor:pointer;user-select:none} #pm-panel h3:hover{color:#b48cff}
  #pm-panel.collapsed{width:auto;padding:6px 12px} #pm-panel.collapsed h3{margin:0} #pm-panel .tabs button{background:#24272e;color:#ccc;border:1px solid #3a3d45;border-radius:6px;padding:3px 8px;margin-right:4px;cursor:pointer}
  #pm-panel .tabs button.on{background:#b48cff;color:#111;border-color:#b48cff}
  #pm-panel .row{display:grid;grid-template-columns:1fr auto auto;gap:4px 10px;padding:6px 2px;border-bottom:1px solid #2a2d33;cursor:pointer}
  #pm-panel .row:hover{background:#ffffff0d} #pm-panel .q{grid-column:1/-1;color:#cfd2d8}
  #pm-panel .pmv{color:#8fb8ff;font-variant-numeric:tabular-nums} #pm-panel .sev{color:#b48cff;font-weight:600;font-variant-numeric:tabular-nums}
  #pm-panel .res{font-weight:600} #pm-panel .yes{color:#ff6b61} #pm-panel .no{color:#3fb27f} #pm-panel .note{color:#9aa0a8;font-size:12px;margin:6px 0}
  #pm-panel .big{font-size:22px;font-weight:700;color:#b48cff} #pm-panel .sim{color:#ffb020}
  #damage-badge{position:fixed;left:50%;transform:translateX(-50%);top:58px;z-index:5;background:#ffb020;color:#111;font:600 12px system-ui;padding:3px 10px;border-radius:12px}`;
  document.head.appendChild(style);
  const badge = document.createElement("div");
  badge.id = "damage-badge";
  badge.hidden = true;
  document.body.appendChild(badge);
  let tab = "open";
  let collapsed = false;
  try { collapsed = localStorage.getItem("pm-collapsed") === "1"; } catch { /* ignore */ }
  let damageOn = false;

  function renderPanel() {
    if (!pm) { panel.innerHTML = "Loading Polymarket data…"; return; }
    const t = pm.track;
    let body = "";
    if (tab === "open") {
      body = `<div class="note">Open Polymarket territorial markets. Polymarket = last traded price (${esc(pm.open[0]?.price_at ?? "")} UTC).
        Seraphim = P(event by deadline) from the DeepState-geometry model as of ${esc(pm.as_of)}. Click to zoom.</div>` +
        pm.open.slice(0, 25).map((m, i) => `<div class="row" data-i="${i}"><div class="q">${esc(m.question)}</div>
          <span>Polymarket <b class="pmv">${pct(m.market)}</b></span><span>Seraphim <b class="sev">${pct(m.ours)}</b></span>
          <span style="color:#9aa0a8">${m.dist_km >= 0 ? "behind" : Math.abs(m.dist_km) + " km from"} line</span></div>`).join("");
    } else {
      body = `<div>Number of markets where Seraphim beats Polymarket: <span class="big">${t.better}/${t.markets}</span></div>
        <div class="note">${esc(t.note)} ${t.market_days.toLocaleString()} market-days.</div>` +
        t.examples.map((r) => `<div class="row" data-name="${esc(r.name)}"><div class="q">${esc(r.question)} <span style="color:#9aa0a8">· ${esc(r.day)}, ${r.h} d before deadline</span></div>
          <span>Polymarket <b class="pmv">${pct(r.market)}</b></span><span>Seraphim <b class="sev">${pct(r.ours)}</b></span>
          <span class="res ${r.resolved === "Yes" ? "yes" : "no"}">→ ${r.resolved}</span></div>`).join("") +
        `<div class="note">Where we lost (largest misses):</div>` +
        t.misses.map((r) => `<div class="row"><div class="q">${esc(r.question)} <span style="color:#9aa0a8">· ${esc(r.day)}</span></div>
          <span>Polymarket <b class="pmv">${pct(r.market)}</b></span><span>Seraphim <b class="sev">${pct(r.ours)}</b></span>
          <span class="res ${r.resolved === "Yes" ? "yes" : "no"}">→ ${r.resolved}</span></div>`).join("");
    }
    panel.classList.toggle("collapsed", collapsed);
    if (collapsed) { panel.innerHTML = `<h3 data-toggle="1">▸ Polymarket vs Seraphim <span style="color:#9aa0a8;font-weight:400">· beats Polymarket on ${pm.track.better}/${pm.track.markets} markets</span></h3>`; return; }
    panel.innerHTML = `<h3 data-toggle="1">▾ Polymarket vs Seraphim</h3><div class="tabs"><button data-tab="open" class="${tab === "open" ? "on" : ""}">Open markets now</button><button data-tab="track" class="${tab === "track" ? "on" : ""}">Track record (resolved)</button></div>${body}`;
  }
  panel.addEventListener("click", (e) => {
    if (e.target.closest("[data-toggle]")) {
      collapsed = !collapsed;
      try { localStorage.setItem("pm-collapsed", collapsed ? "1" : "0"); } catch { /* ignore */ }
      renderPanel();
      return;
    }
    const b = e.target.closest("button[data-tab]");
    if (b) { tab = b.dataset.tab; renderPanel(); return; }
    const r = e.target.closest(".row[data-i]");
    if (r && pm) { const m = pm.open[+r.dataset.i]; map.flyTo({ center: [m.lon, m.lat], zoom: 11 }); }
  });
  fetch("/polymarket.json", { cache: "no-store" }).then((r) => r.json()).then((d) => {
    pm = d;
    renderPanel();
    if (map.getSource("pm-open")) map.getSource("pm-open").setData(pmGeo());
  }).catch(() => {});
  const pmGeo = () => ({
    type: "FeatureCollection",
    features: (pm?.open ?? []).filter((m, i, a) => a.findIndex((x) => x.name === m.name) === i).map((m) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [m.lon, m.lat] },
      properties: { label: `${m.name}\nPM ${pct(m.market)} · S ${pct(m.ours)}` },
    })),
  });

  function damageFilter() {
    if (day === ALL) return ["==", ["get", "week"], damageWeeks.at(-1) ?? ""];
    return ["==", ["get", "week"], weekOf(day)];
  }
  function updateDamage() {
    if (map.getLayer("damage")) map.setFilter("damage", damageFilter());
    const w = day === ALL ? damageWeeks.at(-1) : weekOf(day);
    badge.hidden = !damageOn;
    badge.textContent = `SIMULATED building damage · week of ${w ?? "—"}`;
  }
  fetch("/data/damage.geojson", { cache: "no-store" }).then((r) => r.json()).then((d) => {
    damageWeeks = [...new Set(d.features.map((f) => f.properties.week))].sort();
    if (map.getSource("damage")) map.getSource("damage").setData(d);
    updateDamage();
  }).catch(() => {});

  const damage = {
    id: "sera-damage", group: "analysis", label: "Building damage (SIMULATED satellite)", swatch: "#ffb020", defaultOn: false,
    install(m) {
      if (!m.hasImage("dmg-square")) {
        const s = 12, data = new Uint8Array(s * s * 4);
        for (let y = 0; y < s; y++) for (let x = 0; x < s; x++) {
          const edge = x < 2 || y < 2 || x >= s - 2 || y >= s - 2, o = (y * s + x) * 4;
          data.set(edge ? [20, 20, 20, 255] : [255, 176, 32, 235], o);
        }
        m.addImage("dmg-square", { width: s, height: s, data });
      }
      if (!m.getSource("damage")) m.addSource("damage", { type: "geojson", data: "/data/damage.geojson" });
      if (!m.getLayer("damage")) m.addLayer({ id: "damage", type: "symbol", source: "damage", filter: damageFilter(),
        layout: { "icon-image": "dmg-square", "icon-allow-overlap": true, "icon-size": ["interpolate", ["linear"], ["zoom"], 6, 0.5, 10, 0.9, 14, 1.3] } });
    },
    apply(m, on) {
      damageOn = on;
      if (m.getLayer("damage")) m.setLayoutProperty("damage", "visibility", on ? "visible" : "none");
      updateDamage();
    },
  };
  const polymarket = {
    id: "sera-pm", group: "analysis", label: "Polymarket vs Seraphim", swatch: "#8fb8ff", defaultOn: true,
    install(m) {
      if (!m.getSource("pm-open")) m.addSource("pm-open", { type: "geojson", data: pmGeo() });
      if (!m.getLayer("pm-open")) m.addLayer({ id: "pm-open", type: "circle", source: "pm-open",
        paint: { "circle-radius": 6, "circle-color": "#8fb8ff", "circle-stroke-color": "#b48cff", "circle-stroke-width": 2.5 } });
      if (!m.getLayer("pm-open-label")) m.addLayer({ id: "pm-open-label", type: "symbol", source: "pm-open", minzoom: 7,
        layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Medium"], "text-size": 11, "text-offset": [0, 1.4], "text-anchor": "top" },
        paint: { "text-color": "#ffffff", "text-halo-color": "#000000", "text-halo-width": 1.4 } });
    },
    apply(m, on) {
      for (const id of ["pm-open", "pm-open-label"]) if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
      panel.hidden = !on;
    },
  };
  return {
    overlays: [damage, polymarket],
    setDay(d) { day = d; updateDamage(); },
  };
}
