// H3 hexagon overlays (https://h3geo.org):
//
// 1. "H3 grid": the hexagons covering the visible part of the Ukraine region at a chosen resolution
//    (default 6, ~36 km² per cell). Generated for the viewport on every move; if that would be too many
//    cells, it asks you to zoom in. Click a cell for its H3 index, area and centre.
//
// 2. "Capture probability (model)": only if /data/h3/predictions.json exists. Colours each cell by the
//    model's probability that Russia captures it by the selected horizon. File format (see README):
//      {
//        "model": "name or version", "generated": "2026-11-07T00:00:00Z", "asOf": "2026-11-07",
//        "resolution": 6,
//        "horizons": ["2026-12-07", "2027-02-07"],          // dates the probabilities refer to
//        "cells": { "861e6a2b7ffffff": [0.12, 0.31], ... }   // H3 index -> P(captured by horizon i)
//      }
import { polygonToCells, cellToBoundary, cellToLatLng, getResolution, getHexagonAreaAvg, cellArea, isValidCell } from "h3-js";
import { firstSymbolLayerId } from "./overlays.js";

export const H3_RESOLUTIONS = [4, 5, 6, 7, 8];
export const PROB_COLOR = "#b48cff"; // one hue; opacity carries the probability
const REGION = [22.0, 44.0, 40.3, 52.5]; // west, south, east, north: Ukraine + margin (same as the tiles)
const MAX_CELLS = 40000;
const EMPTY = { type: "FeatureCollection", features: [] };
const fmtKm2 = (x) => (x >= 100 ? Math.round(x).toLocaleString() : x.toFixed(x >= 10 ? 1 : 2));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** Hexagon as a GeoJSON polygon ([lng, lat], closed ring). */
const hexFeature = (h, properties = {}) => ({
  type: "Feature",
  properties: { h3: h, ...properties },
  geometry: { type: "Polygon", coordinates: [cellToBoundary(h, true)] },
});

/**
 * @param {{ onDetail?: (id: string, text: string) => void, onControls?: () => void }} opts
 * Returns { overlays, controlsHtml(), handleControl(el), popupHtml(feature), clickLayers }.
 */
export function h3Overlays({ onDetail, onControls } = {}) {
  const state = { res: 6, on: false, predOn: false, horizon: 0, pred: null, map: null, gridData: EMPTY, predData: EMPTY, tooMany: false };
  let timer = null;

  function refreshGrid() {
    const m = state.map;
    if (!m || !state.on) return;
    const b = m.getBounds();
    const w = Math.max(REGION[0], b.getWest()), e = Math.min(REGION[2], b.getEast());
    const s = Math.max(REGION[1], b.getSouth()), n = Math.min(REGION[3], b.getNorth());
    let features = [];
    state.tooMany = false;
    if (w < e && s < n) {
      // Estimate first, so a country-wide view at a fine resolution doesn't freeze the page.
      const km2 = (e - w) * 111.32 * Math.cos((((s + n) / 2) * Math.PI) / 180) * (n - s) * 110.57;
      if (km2 / getHexagonAreaAvg(state.res, "km2") > MAX_CELLS) state.tooMany = true;
      else {
        const cells = polygonToCells([[[w, s], [e, s], [e, n], [w, n], [w, s]]], state.res, true);
        features = cells.map((h) => hexFeature(h));
      }
    }
    state.gridData = { type: "FeatureCollection", features };
    m.getSource("h3-grid")?.setData(state.gridData);
    onDetail?.("h3-grid", state.tooMany ? `res ${state.res} · zoom in` : `res ${state.res} · ${features.length.toLocaleString()} cells`);
  }
  const scheduleGrid = () => { clearTimeout(timer); timer = setTimeout(refreshGrid, 120); };

  function buildPred() {
    const p = state.pred;
    if (!p) return;
    const k = Math.min(state.horizon, (p.horizons?.length ?? 1) - 1);
    const features = [];
    for (const [h, probs] of Object.entries(p.cells ?? {})) {
      if (!isValidCell(h)) continue;
      const v = Array.isArray(probs) ? probs[k] : probs;
      if (typeof v !== "number" || !(v >= 0 && v <= 1)) continue;
      features.push(hexFeature(h, { p: v }));
    }
    state.predData = { type: "FeatureCollection", features };
    state.map?.getSource("h3-pred")?.setData(state.predData);
    onDetail?.("h3-pred", `${features.length.toLocaleString()} cells · res ${p.resolution ?? "?"}`);
  }

  // Predictions are optional: the overlay only appears once a model has written the file.
  const predReady = fetch("/data/h3/predictions.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((p) => { if (p?.cells) { state.pred = p; buildPred(); } return state.pred; })
    .catch(() => null);

  const grid = {
    id: "h3-grid", group: "analysis", label: "H3 hexagon grid", swatch: "#9aa4b2", defaultOn: false,
    install(m) {
      state.map = m;
      if (!m.getSource("h3-grid")) {
        m.addSource("h3-grid", { type: "geojson", data: state.gridData });
        const before = firstSymbolLayerId(m);
        // Transparent fill so a click anywhere inside a cell hits it.
        m.addLayer({ id: "h3-grid-fill", type: "fill", source: "h3-grid", paint: { "fill-color": "#ffffff", "fill-opacity": 0 } }, before);
        m.addLayer({ id: "h3-grid-line", type: "line", source: "h3-grid",
          paint: { "line-color": "#c9d1dc", "line-opacity": 0.45, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.3, 9, 0.8, 13, 1.4] } }, before);
        m.addSource("h3-selected", { type: "geojson", data: EMPTY });
        m.addLayer({ id: "h3-selected-line", type: "line", source: "h3-selected", paint: { "line-color": "#ffffff", "line-width": 2.5 } }, before);
      }
      if (!grid.bound) { grid.bound = true; m.on("moveend", scheduleGrid); }
    },
    apply(m, on) {
      state.on = on;
      for (const id of ["h3-grid-fill", "h3-grid-line", "h3-selected-line"]) if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
      if (on) refreshGrid(); else onDetail?.("h3-grid", "");
      onControls?.();
    },
  };

  const pred = {
    id: "h3-pred", group: "analysis", label: "Capture probability (model)", swatch: PROB_COLOR, defaultOn: true,
    install(m) {
      state.map = m;
      if (m.getSource("h3-pred")) return;
      m.addSource("h3-pred", { type: "geojson", data: state.predData });
      m.addLayer({ id: "h3-pred-fill", type: "fill", source: "h3-pred",
        paint: { "fill-color": PROB_COLOR, "fill-opacity": ["interpolate", ["linear"], ["get", "p"], 0, 0, 0.05, 0.08, 1, 0.85] } }, firstSymbolLayerId(m));
    },
    apply(m, on) {
      state.predOn = on;
      if (m.getLayer("h3-pred-fill")) m.setLayoutProperty("h3-pred-fill", "visibility", on ? "visible" : "none");
      onControls?.();
    },
  };

  function controlsHtml() {
    const parts = [];
    if (state.on)
      parts.push(`<label>H3 resolution <select data-h3="res">${H3_RESOLUTIONS.map((r) =>
        `<option value="${r}" ${r === state.res ? "selected" : ""}>${r} (~${fmtKm2(getHexagonAreaAvg(r, "km2"))} km²)</option>`).join("")}</select></label>`);
    const p = state.pred;
    if (p && state.predOn) {
      const hs = p.horizons ?? [];
      parts.push(`<div class="h3-pred">
        ${hs.length > 1 ? `<label>Captured by <select data-h3="horizon">${hs.map((h, i) => `<option value="${i}" ${i === state.horizon ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></label>`
          : hs.length ? `<span>Captured by ${esc(hs[0])}</span>` : ""}
        <div class="h3-ramp"><span>0%</span><i style="background:linear-gradient(90deg, transparent, ${PROB_COLOR})"></i><span>100%</span></div>
        <div class="fc-muted">${esc(p.model ?? "model")}${p.asOf ? ` · as of ${esc(p.asOf)}` : ""}</div>
      </div>`);
    }
    return parts.join("");
  }

  function handleControl(el) {
    if (el.dataset.h3 === "res") { state.res = Number(el.value); refreshGrid(); return true; }
    if (el.dataset.h3 === "horizon") { state.horizon = Number(el.value); buildPred(); return true; }
    return false;
  }

  /** Popup for a clicked cell (from either layer); also outlines it. */
  function popupHtml(feature) {
    const h = feature.properties.h3;
    const [lat, lng] = cellToLatLng(h);
    state.map?.getSource("h3-selected")?.setData({ type: "FeatureCollection", features: [hexFeature(h)] });
    const p = state.pred, probs = p?.cells?.[h];
    const rows = probs != null
      ? (Array.isArray(probs) ? probs : [probs]).map((v, i) => `<div class="ev-time">P(captured by ${esc(p.horizons?.[i] ?? "horizon")}): <b>${Math.round(v * 100)}%</b></div>`).join("")
      : "";
    return `<div class="ev-time">H3 cell · resolution ${getResolution(h)}</div>
      <strong class="h3-id">${esc(h)}</strong>
      <div class="ev-time">${fmtKm2(cellArea(h, "km2"))} km² · centre ${lat.toFixed(4)}, ${lng.toFixed(4)}</div>${rows}`;
  }

  return { grid, pred, predReady, controlsHtml, handleControl, popupHtml, clickLayers: ["h3-pred-fill", "h3-grid-fill"] };
}
