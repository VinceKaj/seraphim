#!/usr/bin/env python3
"""
building_damage.py  -  Screen building footprints for likely destruction using
before/after satellite imagery.

Signals combined per building
  * Radiometric normalisation : per-channel gain/offset fitted to the building's
                                surrounding ring (robust median/MAD), so contrast,
                                brightness and colour-temperature differences between
                                the two dates cancel before any change is measured
  * Gradient / texture change : edge-orientation coherence, edge strength,
                                Laplacian roughness, gradient-map correlation, all
                                normalised by the ring's own gradient level
  * Colour change             : fraction of footprint pixels whose CIELAB delta-E
                                exceeds a ring-calibrated threshold
  * Neighbour normalisation   : every change feature is converted to a robust
                                z-score vs the ~25 nearest buildings, so haze,
                                season, cloud shadow and sun-angle shifts that hit
                                a whole area do not count as damage
  * Agreement                 : "colour changed" alone (repaint) or "texture changed"
                                alone is down-weighted; real destruction moves both
  * Optional supervised model : random forest trained on your labels, validated
                                with SPATIAL block cross-validation (no leakage
                                between adjacent buildings)

Usage
  python building_damage.py auto --place "Bakhmut, Ukraine" --out out_bakhmut   # downloads everything itself
  python building_damage.py fetch --place "Bakhmut, Ukraine" --out data         # download only
  python building_damage.py demo --out out_demo [--perturb]   # synthetic proof-of-concept
  python building_damage.py run  --before b.tif --after a.tif \
         --footprints buildings.geojson --out out_real \
         [--label-field damage] [--footprint-crs EPSG:4326]

Output is a TRIAGE list ("likely destroyed" / "review" / "intact"), not ground truth.
Always eyeball the montage and validate against UNOSAT / xBD / Conflict Observatory.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.color import rgb2gray, rgb2lab
from skimage.draw import polygon as draw_polygon
from skimage.registration import phase_cross_correlation

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

STRUCT_FEATS = ["d_coh", "d_lap", "d_gstd", "ncc_loss", "corr_loss"]
GRADIENT_ONLY = ["d_coh", "d_lap", "d_gstd", "corr_loss"]
COLOUR_FEATS = ["changed_frac", "mean_de"]


@dataclass
class Config:
    pad: int = 12                 # px of context around each footprint
    de_thresh: float = 12.0       # fallback CIELAB delta-E if the ring is too small
    k_neighbours: int = 25        # neighbours for local normalisation
    min_pixels: int = 40          # footprints smaller than this are skipped
    w_struct: float = 1.0         # weight of structure evidence in heuristic score
    w_colour: float = 0.6         # weight of colour evidence
    score_mid: float = 2.4        # logistic midpoint on the combined z-score
    score_temp: float = 0.6       # logistic temperature
    review_cut: float = 0.50      # score >= this -> "review"
    destroyed_cut: float = 0.80   # score >= this -> "likely_destroyed"
    cv_block_px: int = 300        # spatial CV block size in pixels
    seed: int = 0


@dataclass
class Building:
    id: int
    rows: np.ndarray              # polygon exterior, pixel coords (row)
    cols: np.ndarray              # polygon exterior, pixel coords (col)
    geom: dict | None = None      # original GeoJSON geometry (for output)
    props: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Core image analysis
# --------------------------------------------------------------------------- #

def _imagery_quality(arr: np.ndarray, name: str) -> tuple[bool, str]:
    """Reject imagery that is too hazy/washed-out to analyse."""
    gray = rgb2gray(arr)
    valid = gray > 0.02
    if valid.sum() < 1000:
        return False, f"{name}: image is nearly all black/empty"
    lo, hi = np.percentile(gray[valid], (5, 95))
    contrast = float(hi - lo)
    g = ndi.gaussian_filter(gray, 0.7)
    mag = np.hypot(ndi.sobel(g, axis=1), ndi.sobel(g, axis=0))
    edge_p99 = float(np.percentile(mag[valid], 99))
    if contrast < 0.18:
        return False, f"{name}: contrast {contrast:.3f} too low (haze/washout) — need a clearer capture"
    if edge_p99 < 0.06:
        return False, f"{name}: edge strength p99 {edge_p99:.3f} too low — no usable texture"
    return True, f"{name}: contrast {contrast:.3f}, edge p99 {edge_p99:.3f}"


def _sobel(gray: np.ndarray):
    g = ndi.gaussian_filter(gray, 0.7)
    return ndi.sobel(g, axis=1), ndi.sobel(g, axis=0)


def robust_stats(x: np.ndarray) -> tuple[float, float]:
    med = float(np.median(x))
    mad = 1.4826 * float(np.median(np.abs(x - med)))
    return med, max(mad, 1e-4)


def radiometric_normalise(bef: np.ndarray, aft: np.ndarray, ring: np.ndarray,
                          min_px: int = 50) -> np.ndarray:
    """
    Map each channel of `aft` onto `bef` using robust stats of the surrounding ring.
    Corrects per-channel gain (contrast / white balance) and offset (brightness).
    The ring is used instead of the whole patch so changed pixels inside the
    footprint do not bias the fit.
    """
    out = np.empty_like(aft)
    for c in range(aft.shape[-1]):
        b, a = bef[..., c][ring], aft[..., c][ring]
        if b.size < min_px or a.size < min_px:
            out[..., c] = aft[..., c]
            continue
        mb, sb = robust_stats(b)
        ma, sa = robust_stats(a)
        gain = np.clip(sb / sa, 0.5, 2.0)      # guard against amplifying noise
        out[..., c] = (aft[..., c] - ma) * gain + mb
    return np.clip(out, 0, 1)


def texture_maps(gray: np.ndarray, ring: np.ndarray):
    """Gradient and Laplacian maps, each divided by the ring's own typical level."""
    g = ndi.gaussian_filter(gray, 0.7)
    gx, gy = ndi.sobel(g, axis=1), ndi.sobel(g, axis=0)
    mag = np.hypot(gx, gy)
    lap = ndi.laplace(g)
    if ring.sum() > 20:
        s_m = np.median(mag[ring]) + 1e-4
        s_l = np.median(np.abs(lap[ring])) + 1e-4
    else:
        s_m = mag.mean() + 1e-4
        s_l = np.abs(lap).mean() + 1e-4
    return gx / s_m, gy / s_m, mag / s_m, lap / s_l


def coherence(gx: np.ndarray, gy: np.ndarray) -> float:
    jxx, jyy, jxy = (gx ** 2).mean(), (gy ** 2).mean(), (gx * gy).mean()
    return float(np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-12))


def pair_features(bef: np.ndarray, aft: np.ndarray, mask: np.ndarray, cfg: Config) -> dict:
    eps = 1e-9
    ring = ~ndi.binary_dilation(mask, iterations=3)
    m = ndi.binary_dilation(mask, iterations=1)

    naive = float(np.linalg.norm(bef[mask].mean(0) - aft[mask].mean(0)))

    # --- radiometric normalisation (contrast, brightness, colour temperature)
    aft_n = radiometric_normalise(bef, aft, ring)

    # --- texture, each image relative to its own surroundings
    gb, ga = rgb2gray(bef), rgb2gray(aft_n)
    gxb, gyb, mgb, lb = texture_maps(gb, ring)
    gxa, gya, mga, la = texture_maps(ga, ring)

    coh_b, coh_a = coherence(gxb[m], gyb[m]), coherence(gxa[m], gya[m])
    gstd_b, gstd_a = mgb[m].std(), mga[m].std()
    lap_b, lap_a = lb[m].var(), la[m].var()

    p, q = gb[m], ga[m]
    ncc = float(np.corrcoef(p, q)[0, 1]) if p.std() > eps and q.std() > eps else 0.0
    x, y = mgb[m], mga[m]
    corr = float(np.corrcoef(x, y)[0, 1]) if x.std() > eps and y.std() > eps else 0.0

    # --- colour, threshold calibrated from the ring's own delta-E distribution
    de = np.linalg.norm(rgb2lab(bef) - rgb2lab(aft_n), axis=-1)
    if ring.sum() > 20:
        r_de = de[ring]
        r_med = float(np.median(r_de))
        r_mad = 1.4826 * float(np.median(np.abs(r_de - r_med)))
        thr = max(0.5 * cfg.de_thresh, r_med + 3.0 * r_mad)
    else:
        thr = cfg.de_thresh
    changed = float((de[mask] > thr).mean())

    return {
        "area_px": int(mask.sum()),
        "naive_dcolour": naive,
        "coh_before": coh_b, "coh_after": coh_a,
        "gstd_before": gstd_b, "gstd_after": gstd_a,
        "lap_before": lap_b, "lap_after": lap_a,
        "d_coh": coh_b - coh_a,
        "d_lap": abs(np.log((lap_a + eps) / (lap_b + eps))),
        "d_gstd": abs(np.log((gstd_a + eps) / (gstd_b + eps))),
        "ncc_loss": 1.0 - ncc,
        "corr_loss": 1.0 - corr,
        "changed_frac": changed,
        "ring_changed": float((de[ring] > thr).mean()) if ring.any() else 0.0,
        "mean_de": float(de[mask].mean()),
    }


def coregister(before: np.ndarray, after: np.ndarray, up: int = 20):
    gb = np.hypot(*_sobel(rgb2gray(before)))
    ga = np.hypot(*_sobel(rgb2gray(after)))
    shift, _, _ = phase_cross_correlation(gb, ga, upsample_factor=up)
    aligned = ndi.shift(after, (shift[0], shift[1], 0), order=1, mode="nearest")
    return np.clip(aligned, 0, 1), shift


# --------------------------------------------------------------------------- #
# Crop providers
# --------------------------------------------------------------------------- #

def _bbox(b: Building):
    return (int(np.floor(b.rows.min())), int(np.ceil(b.rows.max())) + 1,
            int(np.floor(b.cols.min())), int(np.ceil(b.cols.max())) + 1)


def _local_mask(b: Building, r0: int, c0: int, h: int, w: int) -> np.ndarray:
    m = np.zeros((h, w), bool)
    rr, cc = draw_polygon(b.rows - r0, b.cols - c0, shape=(h, w))
    m[rr, cc] = True
    return m


class ArrayProvider:
    def __init__(self, before: np.ndarray, after: np.ndarray, pad: int):
        self.b, self.a, self.pad = before, after, pad

    def get(self, bld: Building):
        r0, r1, c0, c1 = _bbox(bld)
        H, W = self.b.shape[:2]
        r0, c0 = max(0, r0 - self.pad), max(0, c0 - self.pad)
        r1, c1 = min(H, r1 + self.pad), min(W, c1 + self.pad)
        return (self.b[r0:r1, c0:c1], self.a[r0:r1, c0:c1],
                _local_mask(bld, r0, c0, r1 - r0, c1 - c0), (r0, c0))


class RasterProvider:
    MARGIN = 4

    def __init__(self, before_path, after_path, pad, shift=(0.0, 0.0)):
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.vrt import WarpedVRT
        self.sb = rasterio.open(before_path)
        sa = rasterio.open(after_path)
        self.sa = WarpedVRT(sa, crs=self.sb.crs, transform=self.sb.transform,
                            width=self.sb.width, height=self.sb.height,
                            resampling=Resampling.bilinear)
        self.pad, self.shift = pad, shift

    @staticmethod
    def _idx(ds):
        return [1, 2, 3] if ds.count >= 3 else [1]

    @staticmethod
    def _scale(ds):
        # Fixed scale from the dtype, not a per-image percentile stretch, so the
        # two dates are not stretched differently before comparison.
        return {"uint8": 255.0, "uint16": 65535.0}.get(ds.dtypes[0], 1.0)

    def _read(self, ds, r0, r1, c0, c1):
        from rasterio.windows import Window
        H, W = ds.height, ds.width
        rr0, rr1, cc0, cc1 = max(0, r0), min(H, r1), max(0, c0), min(W, c1)
        if rr1 <= rr0 or cc1 <= cc0:
            return None
        a = ds.read(self._idx(ds), window=Window(cc0, rr0, cc1 - cc0, rr1 - rr0)).astype(np.float32)
        a = np.pad(a, ((0, 0), (rr0 - r0, r1 - rr1), (cc0 - c0, cc1 - cc1)), mode="edge")
        if a.shape[0] == 1:
            a = np.repeat(a, 3, axis=0)
        return np.clip(a.transpose(1, 2, 0) / self._scale(ds), 0, 1)

    def get(self, bld: Building):
        r0, r1, c0, c1 = _bbox(bld)
        m = self.pad + self.MARGIN
        R0, R1, C0, C1 = r0 - m, r1 + m, c0 - m, c1 + m
        bef = self._read(self.sb, R0, R1, C0, C1)
        aft = self._read(self.sa, R0, R1, C0, C1)
        if bef is None or aft is None:
            return None
        if abs(self.shift[0]) > 1e-3 or abs(self.shift[1]) > 1e-3:
            aft = np.clip(ndi.shift(aft, (self.shift[0], self.shift[1], 0), order=1, mode="nearest"), 0, 1)
        k = self.MARGIN
        bef, aft = bef[k:-k, k:-k], aft[k:-k, k:-k]
        origin = (R0 + k, C0 + k)
        return bef, aft, _local_mask(bld, origin[0], origin[1], bef.shape[0], bef.shape[1]), origin

    def estimate_shift(self, buildings, n=8, win=512):
        rng = np.random.default_rng(0)
        picks = rng.choice(len(buildings), size=min(n, len(buildings)), replace=False)
        shifts = []
        for i in picks:
            b = buildings[i]
            r, c = int(b.rows.mean()), int(b.cols.mean())
            R0, C0 = r - win // 2, c - win // 2
            bef = self._read(self.sb, R0, R0 + win, C0, C0 + win)
            aft = self._read(self.sa, R0, R0 + win, C0, C0 + win)
            if bef is None or aft is None:
                continue
            try:
                _, s = coregister(bef, aft)
                if np.abs(s).max() < 20:
                    shifts.append(s)
            except Exception:
                pass
        return tuple(np.median(shifts, axis=0)) if shifts else (0.0, 0.0)


# --------------------------------------------------------------------------- #
# Analysis, scoring, supervised model
# --------------------------------------------------------------------------- #

def analyse(provider, buildings: list[Building], cfg: Config, verbose=True) -> pd.DataFrame:
    rows = []
    t0 = time.time()
    for i, b in enumerate(buildings):
        rec = {"id": b.id, "cy": float(b.rows.mean()), "cx": float(b.cols.mean())}
        out = provider.get(b)
        if out is not None:
            bef, aft, mask, _ = out
            if mask.sum() >= cfg.min_pixels:
                rec.update(pair_features(bef, aft, mask, cfg))
        rows.append(rec)
        if verbose and (i + 1) % 100 == 0:
            print(f"  analysed {i + 1}/{len(buildings)} buildings ({time.time() - t0:.0f}s)")
    return pd.DataFrame(rows)


def neighbour_z(df: pd.DataFrame, cols: list[str], k: int) -> pd.DataFrame:
    """
    Two-sided robust z-score: catches buildings that are *more* changed than
    their neighbours AND *less* changed (e.g. flattened to smooth rubble).
    """
    ok = df[cols].notna().all(axis=1).to_numpy()
    z = pd.DataFrame(np.nan, index=df.index, columns=[f"z_{c}" for c in cols])
    sub = df[ok]
    if len(sub) < 5:
        return z
    xy = sub[["cx", "cy"]].to_numpy()
    kk = min(k + 1, len(sub))
    _, idx = cKDTree(xy).query(xy, k=kk)
    for c in cols:
        v = sub[c].to_numpy()
        nb = v[idx[:, 1:]]
        med = np.median(nb, axis=1)
        mad = 1.4826 * np.median(np.abs(nb - med[:, None]), axis=1)
        gmad = 1.4826 * np.median(np.abs(v - np.median(v)))
        floor = max(0.25 * gmad, 1e-3)
        denom = np.maximum(mad, floor)
        z.loc[sub.index, f"z_{c}"] = np.abs(v - med) / denom
    return z


def _sigmoid(x, mid, temp):
    return 1.0 / (1.0 + np.exp(-(x - mid) / temp))


def add_scores(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    feats = sorted(set(STRUCT_FEATS + COLOUR_FEATS))
    df = pd.concat([df, neighbour_z(df, feats, cfg.k_neighbours)], axis=1)
    clip = lambda cols: np.clip(df[[f"z_{c}" for c in cols]], 0, 6)
    top3 = lambda cols: np.sort(clip(cols).to_numpy(), axis=1)[:, -3:].mean(axis=1)

    ok = df[[f"z_{c}" for c in STRUCT_FEATS]].notna().all(axis=1)
    struct_z = np.where(ok, top3(STRUCT_FEATS), np.nan)
    grad_z = np.where(ok, top3(GRADIENT_ONLY), np.nan)
    colour_z = clip(COLOUR_FEATS).mean(axis=1)

    # Scene boost for homogeneous destruction (e.g. Bakhmut)
    global_struct = df[STRUCT_FEATS].mean(axis=1)
    scene_boost = float(np.clip(np.nanmedian(global_struct) / 0.5, 0.0, 2.0))

    combined = struct_z + (cfg.w_colour * colour_z) + (0.5 * scene_boost)

    df["struct_z"] = struct_z
    df["grad_z"] = grad_z
    df["colour_z"] = colour_z
    df["scene_boost"] = scene_boost
    df["score"] = _sigmoid(combined, cfg.score_mid, cfg.score_temp)
    return df


def classify(score: pd.Series, cfg: Config) -> pd.Series:
    out = pd.Series("insufficient_data", index=score.index)
    ok = score.notna()
    out[ok] = "intact"
    out[ok & (score >= cfg.review_cut)] = "review"
    out[ok & (score >= cfg.destroyed_cut)] = "likely_destroyed"
    return out


def supervised(df: pd.DataFrame, label: str, cfg: Config):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import GroupKFold, cross_val_predict

    base = ["d_coh", "d_lap", "d_gstd", "ncc_loss", "corr_loss", "changed_frac", "mean_de",
            "ring_changed", "coh_before", "coh_after", "area_px"]
    cols = base + [c for c in df.columns if c.startswith("z_")]
    ok = df[cols].notna().all(axis=1) & df[label].notna()
    X, y = df.loc[ok, cols].to_numpy(), df.loc[ok, label].astype(int).to_numpy()
    groups = (df.loc[ok, "cx"] // cfg.cv_block_px).astype(int) * 10_000 + (df.loc[ok, "cy"] // cfg.cv_block_px).astype(int)
    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=3, class_weight="balanced",
                                random_state=cfg.seed, n_jobs=-1)
    n_splits = min(5, groups.nunique())
    if n_splits < 2 or len(np.unique(y)) < 2:
        raise ValueError("Not enough spatial blocks / classes for cross-validation.")
    oof = cross_val_predict(rf, X, y, groups=groups, cv=GroupKFold(n_splits=n_splits), method="predict_proba")[:, 1]
    df["score_rf"] = np.nan
    df.loc[ok, "score_rf"] = oof
    rf.fit(X, y)
    imp = pd.Series(rf.feature_importances_, index=cols).sort_values(ascending=False)
    return df, rf, imp


def evaluate(df: pd.DataFrame, label: str, cfg: Config) -> pd.DataFrame:
    from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
    methods = {
        "A  naive mean-colour difference": df["naive_dcolour"],
        "B  normalised colour only": df["colour_z"],
        "C  gradient / texture only": df["grad_z"],
        "D  combined heuristic (no labels)": df["score"],
    }
    if "score_rf" in df:
        methods["E  random forest (spatial CV)"] = df["score_rf"]
    ok = df[label].notna()
    rows = []
    for name, s in methods.items():
        m = ok & s.notna()
        y, v = df.loc[m, label].astype(int), s[m]
        thr_grid = np.quantile(v, np.linspace(0.5, 0.99, 100))
        f1 = max(f1_score(y, v >= t) for t in thr_grid)
        rows.append({"method": name, "ROC_AUC": roc_auc_score(y, v),
                     "PR_AUC": average_precision_score(y, v), "best_F1": f1})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Synthetic scene generator
# --------------------------------------------------------------------------- #
PALETTE = np.array([[0.55, 0.28, 0.22], [0.50, 0.50, 0.52], [0.30, 0.30, 0.33], [0.72, 0.65, 0.52],
                    [0.28, 0.38, 0.52], [0.35, 0.45, 0.30], [0.62, 0.40, 0.28]], dtype=np.float32)
STATES = ["intact", "repainted", "destroyed_rubble", "destroyed_cleared", "partial_heavy", "partial_light"]
STATE_P = [0.72, 0.08, 0.10, 0.03, 0.03, 0.04]
DESTROYED_STATES = {"destroyed_rubble", "destroyed_cleared", "partial_heavy"}


def _noise(rng, shape, sigma):
    n = ndi.gaussian_filter(rng.standard_normal(shape), sigma)
    return (n / (n.std() + 1e-9)).astype(np.float32)


def _uv(b, r0, r1, c0, c1):
    yy, xx = np.mgrid[r0:r1, c0:c1]
    dx, dy = xx - b["cx"], yy - b["cy"]
    ct, st = np.cos(b["th"]), np.sin(b["th"])
    return dx * ct + dy * st, -dx * st + dy * ct


def _window(b, shape, margin):
    r = np.hypot(b["a"], b["b"]) + margin
    return (max(0, int(b["cy"] - r)), min(shape[0], int(b["cy"] + r) + 1),
            max(0, int(b["cx"] - r)), min(shape[1], int(b["cx"] + r) + 1))


def _roof(b, u, v, colour, rng):
    shape = u.shape
    img = np.empty(shape + (3,), np.float32)
    shade = np.where(v > 0, 1.07, 0.93)
    stripes = 0.03 * np.sin(v * 2 * np.pi / 3.0)
    tex = _noise(rng, shape, 1.2) * 0.02
    for c in range(3):
        img[..., c] = colour[c] * shade * (1 + stripes) + tex
    img[np.abs(v) < 0.8] *= 0.82
    img[(np.abs(u) > b["a"] - 1.2) | (np.abs(v) > b["b"] - 1.2)] *= 0.72
    return img


def _rubble(colour, shape, rng, amp):
    mean = 0.5 * colour + 0.5 * np.array([0.45, 0.42, 0.38], np.float32)
    n = 0.5 * _noise(rng, shape, 0.8) + 0.7 * _noise(rng, shape, 1.6) + 0.6 * _noise(rng, shape, 3.2)
    img = mean[None, None, :] * (1 + amp * n[..., None] * 2.2)
    img[_noise(rng, shape, 2.2) < -1.0] *= 0.45
    return np.clip(img, 0, 1)


def make_scene(seed=0, size=1200, cell=44):
    rng = np.random.default_rng(seed)
    H = W = size
    ground = np.empty((H, W, 3), np.float32)
    lf = _noise(rng, (H, W), 25) * 0.035 + _noise(rng, (H, W), 6) * 0.02
    grain = _noise(rng, (H, W), 1.0) * 0.015
    base = np.array([0.40, 0.42, 0.28], np.float32)
    for c in range(3):
        ground[..., c] = base[c] + lf * (1.0, 1.0, 0.8)[c] + grain
    for k in range(0, size, 4 * cell):
        for sl in (np.s_[max(0, k - 4):k + 4, :], np.s_[:, max(0, k - 4):k + 4]):
            ground[sl] = np.array([0.36, 0.36, 0.37], np.float32) + grain[sl][..., None]

    blds = []
    bid = 0
    for i in range(size // cell):
        for j in range(size // cell):
            if rng.random() > 0.8:
                continue
            a, bh = rng.uniform(6, 12), rng.uniform(5, 10)
            th = np.deg2rad(rng.uniform(-25, 25)) if rng.random() < 0.3 else 0.0
            cy = (i + 0.5) * cell + rng.uniform(-2, 2)
            cx = (j + 0.5) * cell + rng.uniform(-2, 2)
            state = rng.choice(STATES, p=STATE_P)
            roof = PALETTE[rng.integers(len(PALETTE))] * rng.uniform(0.9, 1.1)
            new_roof = PALETTE[rng.integers(len(PALETTE))] * rng.uniform(0.9, 1.1)
            blds.append(dict(id=bid, cx=cx, cy=cy, a=a, b=bh, th=th, state=str(state),
                             roof=np.clip(roof, 0, 1), new_roof=np.clip(new_roof, 0, 1),
                             frac=rng.uniform(0.55, 0.8), amp=rng.uniform(0.05, 0.2)))
            bid += 1

    def render(after: bool):
        img = ground.copy()
        off, strength = ((-2, 3), 0.78) if after else ((3, 3), 0.72)
        for b in blds:
            if after and b["state"] in ("destroyed_rubble", "destroyed_cleared"):
                continue
            r0, r1, c0, c1 = _window(b, (H, W), 6)
            bs = dict(b, cx=b["cx"] + off[1], cy=b["cy"] + off[0])
            u, v = _uv(bs, r0, r1, c0, c1)
            sh = (np.abs(u) <= b["a"]) & (np.abs(v) <= b["b"])
            img[r0:r1, c0:c1][sh] *= strength
        for b in blds:
            r0, r1, c0, c1 = _window(b, (H, W), 4)
            u, v = _uv(b, r0, r1, c0, c1)
            fp = (np.abs(u) <= b["a"]) & (np.abs(v) <= b["b"])
            brng = np.random.default_rng(seed * 100003 + b["id"])
            st = b["state"] if after else "intact"
            colour = b["new_roof"] if st == "repainted" else b["roof"]
            roof = _roof(b, u, v, colour, brng)
            tgt = img[r0:r1, c0:c1]
            if st in ("intact", "repainted"):
                tgt[fp] = roof[fp]
            elif st == "destroyed_rubble":
                spread = (np.abs(u) <= b["a"] + 2) & (np.abs(v) <= b["b"] + 2)
                rub = _rubble(b["roof"], u.shape, brng, b["amp"])
                tgt[spread] = np.where(fp[spread][:, None], rub[spread], 0.6 * rub[spread] + 0.4 * tgt[spread])
            elif st == "destroyed_cleared":
                scar = tgt * np.array([0.92, 0.88, 0.8], np.float32) + _noise(brng, u.shape, 1.0)[..., None] * 0.01
                tgt[fp] = scar[fp]
            elif st == "partial_heavy":
                tgt[fp] = roof[fp]
                part = fp & (u > b["a"] * (1 - 2 * b["frac"]))
                rub = _rubble(b["roof"], u.shape, brng, b["amp"])
                tgt[part] = rub[part]
            elif st == "partial_light":
                tgt[fp] = roof[fp]
                ru, rv = brng.uniform(-b["a"] * 0.5, b["a"] * 0.5), brng.uniform(-b["b"] * 0.5, b["b"] * 0.5)
                hole = fp & (((u - ru) ** 2 + (v - rv) ** 2) < 3.5 ** 2)
                rub = _rubble(b["roof"], u.shape, brng, b["amp"])
                tgt[hole] = rub[hole]
        return img

    before = render(False)
    after = render(True)

    gain = rng.uniform(0.88, 1.12) * rng.uniform(0.95, 1.05, 3)
    offset = rng.uniform(-0.04, 0.04, 3)
    after = after * gain + offset
    yy, xx = np.mgrid[0:H, 0:W]
    after += 0.06 * np.sin(xx / 400.0 + 1.0)[..., None] * np.cos(yy / 500.0)[..., None]
    for _ in range(2):
        cy, cx, s = rng.uniform(0, H), rng.uniform(0, W), rng.uniform(70, 130)
        after *= (1 - 0.28 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s ** 2)))[..., None]
    true_shift = np.array([1.6, -1.1])
    after = ndi.shift(after, (true_shift[0], true_shift[1], 0), order=1, mode="nearest")
    after += rng.standard_normal(after.shape).astype(np.float32) * 0.015
    before = before + rng.standard_normal(before.shape).astype(np.float32) * 0.008
    before, after = np.clip(before, 0, 1), np.clip(after, 0, 1)

    buildings = []
    for b in blds:
        cs = [(su, sv) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        ct, st = np.cos(b["th"]), np.sin(b["th"])
        xs = np.array([b["cx"] + su * b["a"] * ct - sv * b["b"] * st for su, sv in cs])
        ys = np.array([b["cy"] + su * b["a"] * st + sv * b["b"] * ct for su, sv in cs])
        buildings.append(Building(id=b["id"], rows=ys, cols=xs,
                                  geom={"type": "Polygon", "coordinates": [[[float(x), float(y)] for x, y in zip(xs, ys)] + [[float(xs[0]), float(ys[0])]]]},
                                  props={"true_state": b["state"], "destroyed": int(b["state"] in DESTROYED_STATES)}))
    return before, after, buildings, true_shift


def perturb_radiometry(img, rng, gain_range=(0.6, 1.4), tint_range=(0.9, 1.1), bias=0.08):
    """Simulate a different contrast, colour temperature, and brightness."""
    gain = rng.uniform(*gain_range)
    tint = rng.uniform(*tint_range, 3)
    off = rng.uniform(-bias, bias, 3)
    m = img.mean(axis=(0, 1))
    return np.clip((img - m) * gain * tint + m + off, 0, 1)


# --------------------------------------------------------------------------- #
# Figures / output
# --------------------------------------------------------------------------- #

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


CLS_COLOUR = {"intact": "#2ecc71", "review": "#f1c40f", "likely_destroyed": "#e74c3c", "insufficient_data": "#95a5a6"}


def fig_overview(before, after, buildings, df, path):
    plt = _plt()
    fig, ax = plt.subplots(1, 2, figsize=(18, 9))
    ax[0].imshow(before); ax[0].set_title("BEFORE")
    ax[1].imshow(after); ax[1].set_title("AFTER  -  outlines: green intact / yellow review / red likely destroyed")
    cls = dict(zip(df["id"], df["class"]))
    for b in buildings:
        ax[1].plot(np.append(b.cols, b.cols[0]), np.append(b.rows, b.rows[0]),
                   color=CLS_COLOUR[cls.get(b.id, "insufficient_data")], lw=1.0)
    for a in ax:
        a.axis("off")
    plt.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def fig_montage(provider, buildings, df, ids, title, path, ncols=6, extra_col=None):
    plt = _plt()
    byid = {b.id: b for b in buildings}
    ids = [i for i in ids if provider.get(byid[i]) is not None][: ncols * 4]
    if not ids:
        return
    nrows = int(np.ceil(len(ids) / ncols))
    fig, axes = plt.subplots(nrows, ncols * 2, figsize=(ncols * 2.4, nrows * 1.35 + 0.6), squeeze=False)
    for a in axes.ravel():
        a.axis("off")
    rowd = df.set_index("id")
    for n, i in enumerate(ids):
        bef, aft, _, (r0, c0) = provider.get(byid[i])
        b = byid[i]
        r, c = divmod(n, ncols)
        for k, img in enumerate((bef, aft)):
            ax = axes[r, c * 2 + k]
            ax.imshow(img, interpolation="nearest")
            ax.plot(np.append(b.cols, b.cols[0]) - c0, np.append(b.rows, b.rows[0]) - r0, color="cyan", lw=0.8)
        lab = f"#{i} s={rowd.loc[i, 'score']:.2f}"
        if extra_col and extra_col in rowd:
            lab += f" {rowd.loc[i, extra_col]}"
        axes[r, c * 2].set_title(lab, fontsize=6.5, loc="left")
    fig.suptitle(title + "   (each pair: before | after)", fontsize=10)
    plt.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def fig_roc(df, label, path):
    from sklearn.metrics import roc_curve, roc_auc_score
    plt = _plt()
    series = {"A naive colour": df["naive_dcolour"], "B normalised colour": df["colour_z"],
              "C gradient/texture": df["grad_z"], "D combined heuristic": df["score"]}
    if "score_rf" in df:
        series["E random forest (spatial CV)"] = df["score_rf"]
    fig, ax = plt.subplots(figsize=(6.5, 6))
    for name, s in series.items():
        m = s.notna() & df[label].notna()
        fpr, tpr, _ = roc_curve(df.loc[m, label].astype(int), s[m])
        ax.plot(fpr, tpr, lw=2, label=f"{name} (AUC {roc_auc_score(df.loc[m, label].astype(int), s[m]):.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=0.8)
    ax.set_xlabel("False positive rate"); ax.set_ylabel("True positive rate")
    ax.set_title("Detecting destroyed buildings"); ax.legend(loc="lower right", fontsize=8); ax.grid(alpha=0.3)
    plt.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def fig_states(df, path):
    plt = _plt()
    order = [s for s in STATES if s in set(df["true_state"])]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.5))
    for a, col, t in zip(ax, ["naive_dcolour", "colour_z", "score"],
                         ["A: naive mean-colour change", "B: normalised colour evidence", "D: combined score"]):
        data = [df.loc[df["true_state"] == s, col].dropna() for s in order]
        a.boxplot(data, tick_labels=[s.replace("_", "\n") for s in order], showfliers=False)
        a.set_title(t, fontsize=10); a.tick_params(axis="x", labelsize=7); a.grid(alpha=0.3)
    plt.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def write_geojson(df, buildings, path, extra_props: bool = True):
    byid = {b.id: b for b in buildings}
    feats = []
    for _, r in df.iterrows():
        b = byid[int(r["id"])]
        props = {k: (None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v))
                 for k, v in r.items()} if extra_props else {"id": int(r["id"]), "class": r["class"]}
        for k, v in (b.props or {}).items():
            props.setdefault(k, v)
        feats.append({"type": "Feature", "geometry": b.geom, "properties": props})
    Path(path).write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


# --------------------------------------------------------------------------- #
# Data acquisition
# --------------------------------------------------------------------------- #
import datetime as dt
import hashlib
import io
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "building-damage-triage/1.0 (personal research tool)"
R_EARTH = 6378137.0
ORIGIN = math.pi * R_EARTH
WAYBACK_CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
NOMINATIM = "https://nominatim.openstreetmap.org/search"

ESRI_NOTICE = """
Imagery source: Esri World Imagery Wayback (historical releases of the World Imagery basemap).
It is subject to Esri's Terms of Use / Master Agreement (see https://livingatlas.arcgis.com/wayback/ and
the World Imagery item on ArcGIS Living Atlas). This tool downloads a small, bounded number of tiles for
analysis. YOU are responsible for making sure your use (and any publication of results) complies with those
terms and with attribution requirements. Footprints come from OpenStreetMap (ODbL, (c) OSM contributors).
"""


def _http(url, data=None, timeout=60, retries=3):
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (k + 1))
    raise last


def confirm_esri(accepted: bool):
    if accepted:
        return
    print(ESRI_NOTICE)
    if not sys.stdin.isatty() or input("Continue? [y/N] ").strip().lower() != "y":
        sys.exit("Stopped. Re-run with --accept-esri-terms once you have read the notice above.")


def geocode(place: str):
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    res = json.loads(_http(f"{NOMINATIM}?{q}"))
    if not res:
        sys.exit(f"Could not geocode '{place}'. Try a more specific name or pass --bbox W S E N.")
    return float(res[0]["lon"]), float(res[0]["lat"]), res[0].get("display_name", place)


def bbox_around(lon, lat, size_m):
    half = size_m / 2.0
    dlat = half / 111320.0
    dlon = half / (111320.0 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_range(bbox, z):
    w, s, e, n = bbox
    x0, y1 = lonlat_to_tile(w, s, z)
    x1, y0 = lonlat_to_tile(e, n, z)
    return x0, x1, y0, y1


def merc_to_lonlat(x, y):
    return math.degrees(x / R_EARTH), math.degrees(math.atan(math.sinh(y / R_EARTH)))


def wayback_releases():
    cfg = json.loads(_http(WAYBACK_CONFIG))
    items = cfg.items() if isinstance(cfg, dict) else enumerate(cfg)
    out = []
    for k, v in items:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", v.get("itemTitle", ""))
        url = v.get("itemURL")
        if m and url:
            out.append({"num": str(k), "date": m.group(1), "url": url.replace("{subDomain}", "wayback"),
                        "meta": v.get("metadataLayerUrl")})
    if not out:
        raise RuntimeError("Wayback config had no usable releases (format may have changed).")
    out.sort(key=lambda r: r["date"])
    return out


def get_tile(rel, z, x, y, cache: Path):
    p = cache / rel["num"] / str(z) / f"{x}_{y}.bin"
    if p.exists():
        return p.read_bytes() or None
    url = rel["url"].replace("{level}", str(z)).replace("{row}", str(y)).replace("{col}", str(x))
    try:
        b = _http(url, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        b = b""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b)
    return b or None


def _signature(rel, z, rng, cache):
    x0, x1, y0, y1 = rng
    xm, ym = (x0 + x1) // 2, (y0 + y1) // 2
    pts = {(x0, y0), (x1, y1), (x0, y1), (x1, y0), (xm, ym)}
    return tuple(sorted((c, hashlib.md5(b).hexdigest() if b else None)
                        for c in pts for b in [get_tile(rel, z, c[0], c[1], cache)]))


def choose_releases(rels, before_date, after_date, z, rng, cache):
    pre = [r for r in rels if r["date"] <= before_date]
    post = [r for r in rels if r["date"] <= after_date]
    if not pre:
        sys.exit(f"No Wayback release on/before {before_date}.")
    rb = pre[-1]
    sb = _signature(rb, z, rng, cache)
    for ra in reversed(post):
        if ra["date"] <= rb["date"]:
            break
        if _signature(ra, z, rng, cache) != sb:
            return rb, ra
    sys.exit(f"Esri has no imagery update at this location between {rb['date']} and {after_date}; "
             "before and after would be identical. Try a different place or date range.")


def esri_capture_info(rel, lon, lat):
    base = rel.get("meta")
    if not base:
        return None
    q = urllib.parse.urlencode({"f": "json", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                                "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                "outFields": "SRC_DATE2,SRC_RES,SRC_DESC", "returnGeometry": "false"})
    for layer in range(0, 6):
        try:
            js = json.loads(_http(f"{base}/{layer}/query?{q}", timeout=20, retries=1))
        except Exception:
            continue
        if js.get("features"):
            a = js["features"][0]["attributes"]
            d = a.get("SRC_DATE2")
            return {"capture_date": dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc).strftime("%Y-%m-%d") if d else None,
                    "resolution_m": a.get("SRC_RES"), "source": a.get("SRC_DESC")}
    return None


def fetch_pair(rb, ra, z, rng, cache, workers=6, max_missing=0.2):
    from PIL import Image
    x0, x1, y0, y1 = rng
    coords = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]

    def grab(rel):
        with ThreadPoolExecutor(workers) as ex:
            return list(ex.map(lambda c: get_tile(rel, z, c[0], c[1], cache), coords))
    tb, ta = grab(rb), grab(ra)
    bad = [i for i in range(len(coords)) if tb[i] is None or ta[i] is None]
    if len(bad) > max_missing * len(coords):
        raise RuntimeError(f"{len(bad)}/{len(coords)} tiles unavailable at zoom {z}; try --zoom {z - 1}.")
    first = next(b for b in tb if b is not None)
    T = Image.open(io.BytesIO(first)).size[0]

    def stitch(tiles):
        arr = np.zeros(((y1 - y0 + 1) * T, (x1 - x0 + 1) * T, 3), np.uint8)
        for i, ((x, y), b) in enumerate(zip(coords, tiles)):
            if b is None or i in bad:
                continue
            im = Image.open(io.BytesIO(b)).convert("RGB")
            if im.size != (T, T):
                im = im.resize((T, T))
            arr[(y - y0) * T:(y - y0 + 1) * T, (x - x0) * T:(x - x0 + 1) * T] = np.asarray(im)
        return arr.astype(np.float32) / 255.0

    size_m = 2 * ORIGIN / 2 ** z
    georef = (-ORIGIN + x0 * size_m, ORIGIN - y0 * size_m, size_m / T)
    return stitch(tb), stitch(ta), georef


def mosaic_bounds_lonlat(shape, georef):
    minx, maxy, res = georef
    H, W = shape[:2]
    w, n = merc_to_lonlat(minx, maxy)
    e, s = merc_to_lonlat(minx + W * res, maxy - H * res)
    return (w, s, e, n)


def fetch_osm_buildings(bbox, asof=None):
    w, s, e, n = bbox
    date = f'[date:"{asof}T00:00:00Z"]' if asof else ""
    q = f'[out:json][timeout:180]{date};(way["building"]({s},{w},{n},{e}););out geom;'
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for ep in OVERPASS:
        try:
            js = json.loads(_http(ep, data=body, timeout=240, retries=2))
            if "elements" in js and not ("error" in str(js.get("remark", "")).lower()):
                return js["elements"]
            last = RuntimeError(js.get("remark", "empty response"))
        except Exception as ex:
            last = ex
        print(f"  Overpass endpoint failed ({ep}): {last}")
    raise RuntimeError(f"All Overpass endpoints failed: {last}")


def elements_to_geojson(els):
    feats = []
    for el in els:
        g = el.get("geometry")
        if el.get("type") != "way" or not g or len(g) < 3:
            continue
        ring = [[p["lon"], p["lat"]] for p in g]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"osm_id": el.get("id"), "building": (el.get("tags") or {}).get("building")}})
    return {"type": "FeatureCollection", "features": feats}


def fc_to_buildings(fc, georef, shape):
    minx, maxy, res = georef
    H, W = shape[:2]
    out = []
    for f in fc["features"]:
        ring = np.array(f["geometry"]["coordinates"][0], float)
        x = R_EARTH * np.radians(ring[:, 0])
        y = R_EARTH * np.arcsinh(np.tan(np.radians(ring[:, 1])))
        rows, cols = (maxy - y) / res, (x - minx) / res
        if not (0 <= rows.mean() < H and 0 <= cols.mean() < W):
            continue
        out.append(Building(id=len(out), rows=rows, cols=cols, geom=f["geometry"], props=dict(f["properties"])))
    return out


def write_geotiff(path, arr01, georef):
    import rasterio
    from rasterio.transform import Affine
    minx, maxy, res = georef
    a = (np.clip(arr01, 0, 1) * 255).astype(np.uint8)
    H, W, _ = a.shape
    with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=3, dtype="uint8", crs="EPSG:3857",
                       transform=Affine(res, 0.0, minx, 0.0, -res, maxy), compress="deflate") as dst:
        dst.write(a.transpose(2, 0, 1))


def acquire(args, out: Path):
    confirm_esri(args.accept_esri_terms)
    if args.bbox:
        bbox, name = tuple(args.bbox), "custom bbox"
    else:
        lon, lat, name = geocode(args.place)
        bbox = bbox_around(lon, lat, args.size_km * 1000.0)
    z = args.zoom
    rng = tile_range(bbox, z)
    n_tiles = (rng[1] - rng[0] + 1) * (rng[3] - rng[2] + 1)
    print(f"[acquire] {name}\n          bbox (W,S,E,N) = {np.round(bbox, 5).tolist()}, zoom {z}, {n_tiles} tiles per image")
    if n_tiles > args.max_tiles:
        sys.exit(f"{n_tiles} tiles exceeds --max-tiles {args.max_tiles}. Use a smaller --size-km or raise the limit.")
    cache = out / "tile_cache"
    print("[acquire] reading Wayback release list ...")
    rels = wayback_releases()
    after_date = args.after_date or dt.date.today().isoformat()
    rb, ra = choose_releases(rels, args.before_date, after_date, z, rng, cache)
    print(f"[acquire] BEFORE = Wayback release {rb['date']}   AFTER = Wayback release {ra['date']}")
    cen = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    cb, ca = esri_capture_info(rb, *cen), esri_capture_info(ra, *cen)
    for tag, c in (("before", cb), ("after", ca)):
        if c:
            print(f"          {tag}: captured {c['capture_date']}  ~{c['resolution_m']} m  ({c['source']})  [Esri metadata]")
    if cb and ca and cb.get("capture_date") and ca.get("capture_date"):
        if ca["capture_date"] <= args.before_date:
            print("  WARNING: 'after' imagery was captured on/before your before-date, so it may not show the event.")
        if cb["capture_date"] > args.before_date:
            print("  WARNING: 'before' imagery was captured after your before-date.")
    print("[acquire] downloading + stitching tiles ...")
    before, after, georef = fetch_pair(rb, ra, z, rng, cache)
    mb = mosaic_bounds_lonlat(before.shape, georef)
    asof = args.before_date if args.footprints_asof == "before" else None
    print(f"[acquire] OSM building footprints ({'as of ' + asof if asof else 'current'}) ...")
    try:
        els = fetch_osm_buildings(mb, asof)
    except RuntimeError as ex:
        if not asof:
            raise
        print(f"  historical query failed ({ex}); falling back to CURRENT OSM footprints.")
        print("  NOTE: buildings constructed after the before-date may be reported as 'changed'.")
        asof, els = None, fetch_osm_buildings(mb, None)
    fc = elements_to_geojson(els)
    buildings = fc_to_buildings(fc, georef, before.shape)
    print(f"          {len(buildings)} building footprints inside the imagery")
    if not buildings:
        sys.exit("No building footprints found for this area (OSM may be empty there).")
    info = {"place": name, "bbox_wsen": list(bbox), "zoom": z, "before_release": rb["date"], "after_release": ra["date"],
            "before_capture": cb, "after_capture": ca, "footprints_asof": asof or "current",
            "georef_epsg3857_minx_maxy_res": list(georef), "n_footprints": len(buildings)}
    return before, after, georef, buildings, fc, info


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_demo(args):
    cfg = Config(seed=args.seed)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    print(f"[1/6] Generating synthetic scene (seed {args.seed}) ...")
    before, after_raw, buildings, true_shift = make_scene(seed=args.seed)
    print(f"      {len(buildings)} buildings, injected misregistration {true_shift} px, global gain/offset, "
          f"haze, cloud shadows, repainted roofs, cleared lots, partial damage")
    if getattr(args, "perturb", False):
        after_raw = perturb_radiometry(after_raw, np.random.default_rng(args.seed + 1))
        print("      applied radiometric perturbation (contrast, tint, brightness) to the AFTER image")

    print("[2/6] Co-registering after-image ...")
    after, est = coregister(before, after_raw)
    print(f"      recovered shift {np.round(est, 2)} px (should be ~{-true_shift})")

    print("[3/6] Extracting per-building features ...")
    provider = ArrayProvider(before, after, cfg.pad)
    df = analyse(provider, buildings, cfg)
    meta = pd.DataFrame([{"id": b.id, **b.props} for b in buildings])
    df = df.merge(meta, on="id")

    print("[4/6] Scoring (neighbour-normalised heuristic) ...")
    df = add_scores(df, cfg)
    df["class"] = classify(df["score"], cfg)

    print("[5/6] Supervised model with spatial-block CV ...")
    df, rf, imp = supervised(df, "destroyed", cfg)

    print("[6/6] Evaluating + writing outputs ...")
    ev = evaluate(df, "destroyed", cfg)
    pd.set_option("display.width", 140)
    print("\n=== Ablation: how well does each method separate destroyed from not-destroyed? ===")
    print(ev.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    ok = df["destroyed"].notna() & df["score"].notna()
    cm = pd.crosstab(df.loc[ok, "destroyed"].map({0: "actually intact/minor", 1: "actually destroyed"}),
                     df.loc[ok, "class"])
    print("\n=== Confusion at default cutoffs (heuristic, no labels used) ===")
    print(cm.to_string())

    br = df.groupby("true_state").agg(n=("id", "size"), mean_score=("score", "mean"),
                                      pct_likely_destroyed=("class", lambda s: 100 * (s == "likely_destroyed").mean()),
                                      pct_review_or_worse=("class", lambda s: 100 * (s.isin(["review", "likely_destroyed"])).mean()),
                                      pct_RF_over_50=("score_rf", lambda s: 100 * (s > 0.5).mean()))
    print("\n=== Behaviour by true state (heuristic needs no labels; RF = spatial-CV out-of-fold) ===")
    print(br.to_string(float_format=lambda x: f"{x:.2f}"))
    print("\n=== Top random-forest features ===")
    print(imp.head(8).to_string(float_format=lambda x: f"{x:.3f}"))

    df.to_csv(out / "results.csv", index=False)
    ev.to_csv(out / "ablation.csv", index=False)
    write_geojson(df, buildings, out / "buildings.geojson")
    fig_overview(before, after, buildings, df, out / "overview.png")
    fig_roc(df, "destroyed", out / "roc_ablation.png")
    fig_states(df, out / "score_by_state.png")
    top = df.sort_values("score", ascending=False)
    fig_montage(provider, buildings, df, top["id"].tolist(), "Highest-scoring buildings", out / "montage_top.png", extra_col="true_state")
    wrong = df[(df["destroyed"] == 0) & (df["class"] == "likely_destroyed")].sort_values("score", ascending=False)
    miss = df[(df["destroyed"] == 1) & (df["class"] == "intact")].sort_values("score")
    fig_montage(provider, buildings, df, wrong["id"].tolist(), "False alarms (flagged, actually not destroyed)", out / "montage_false_alarms.png", extra_col="true_state")
    fig_montage(provider, buildings, df, miss["id"].tolist(), "Misses (destroyed, scored intact)", out / "montage_misses.png", extra_col="true_state")
    import joblib
    joblib.dump(rf, out / "model_demo.joblib")
    print(f"\nWrote results to {out.resolve()}")


def _save_png(path, arr):
    from PIL import Image
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).save(path)


def _save_inputs(out, before, after, georef):
    _save_png(out / "before.png", before)
    _save_png(out / "after.png", after)
    try:
        write_geotiff(out / "before.tif", before, georef)
        write_geotiff(out / "after.tif", after, georef)
        return True
    except ImportError:
        return False


def cmd_fetch(args):
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    before, after, georef, buildings, fc, info = acquire(args, out)
    (out / "buildings.geojson").write_text(json.dumps(fc))
    (out / "info.json").write_text(json.dumps(info, indent=2, default=str))
    tif = _save_inputs(out, before, after, georef)
    print(f"\nSaved to {out.resolve()}: buildings.geojson, before/after .png" + (", before/after .tif" if tif else ""))
    if tif:
        print(f"Next: python {Path(sys.argv[0]).name} run --before {out}/before.tif --after {out}/after.tif "
              f"--footprints {out}/buildings.geojson --out {out}/analysis")
    else:
        print("rasterio not installed, so no GeoTIFFs were written. Use the `auto` command instead (needs no rasterio).")


def cmd_auto(args):
    cfg = Config()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    before, after_raw, georef, buildings, fc, info = acquire(args, out)

    # Imagery quality gate
    ok_b, why_b = _imagery_quality(before, "BEFORE")
    ok_a, why_a = _imagery_quality(after_raw, "AFTER")
    print(why_b)
    print(why_a)
    if not ok_a or not ok_b:
        sys.exit(
            f"\nImagery rejected: {why_a if not ok_a else why_b}\n"
            f"Try --after-date <different date> or --zoom 17 to get a clearer capture."
        )

    print("[analyse] co-registering ...")
    after, shift = coregister(before, after_raw)
    info["coregistration_shift_px_rc"] = [float(shift[0]), float(shift[1])]
    print(f"          shift {np.round(shift, 2)} px")
    provider = ArrayProvider(before, after, cfg.pad)
    print(f"[analyse] {len(buildings)} buildings ...")
    df = analyse(provider, buildings, cfg)
    df = add_scores(df, cfg)
    df["class"] = classify(df["score"], cfg)
    counts = df["class"].value_counts().to_dict()
    info["class_counts"] = counts
    df.to_csv(out / "results.csv", index=False)
    write_geojson(df, buildings, out / "buildings.geojson")
    (out / "info.json").write_text(json.dumps(info, indent=2, default=str))
    _save_inputs(out, before, after_raw, georef)
    fig_overview(before, after, buildings, df, out / "overview.png")
    top = df.sort_values("score", ascending=False)
    fig_montage(provider, buildings, df, top["id"].tolist(), "Highest-scoring buildings", out / "montage_top.png")
    mid = df[df["class"] == "review"].sort_values("score", ascending=False)
    fig_montage(provider, buildings, df, mid["id"].tolist(), "'Review' band", out / "montage_review.png")
    print("\n=== RESULT ===")
    for k in ("likely_destroyed", "review", "intact", "insufficient_data"):
        print(f"  {k:18s} {counts.get(k, 0)}")
    print(f"\nOpen {out / 'montage_top.png'} FIRST and judge by eye whether the top-ranked buildings are really destroyed.")
    print(f"Outputs in {out.resolve()}: results.csv, buildings.geojson (QGIS), overview.png, montages, info.json")


def _load_footprints(path, raster_path, footprint_crs, label_field):
    import rasterio
    from rasterio.warp import transform_geom
    fc = json.loads(Path(path).read_text())
    feats = fc["features"] if fc.get("type") == "FeatureCollection" else [fc]
    with rasterio.open(raster_path) as src:
        inv, rcrs = ~src.transform, src.crs
    out = []
    for i, f in enumerate(feats):
        g = f.get("geometry")
        if not g:
            continue
        gt = transform_geom(footprint_crs, rcrs, g) if str(footprint_crs) != str(rcrs) else g
        if gt["type"] == "MultiPolygon":
            ring = max((p[0] for p in gt["coordinates"]), key=len)
        elif gt["type"] == "Polygon":
            ring = gt["coordinates"][0]
        else:
            continue
        pts = np.array([inv * (x, y) for x, y, *_ in ring])
        props = dict(f.get("properties") or {})
        b = Building(id=i, rows=pts[:, 1], cols=pts[:, 0], geom=g, props=props)
        out.append(b)
    return out


def cmd_run(args):
    cfg = Config()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    try:
        import rasterio  # noqa: F401
    except ImportError:
        sys.exit("Real-imagery mode needs rasterio:  pip install rasterio")
    print("Loading footprints ...")
    buildings = _load_footprints(args.footprints, args.before, args.footprint_crs, args.label_field)
    print(f"  {len(buildings)} footprints")
    provider = RasterProvider(args.before, args.after, cfg.pad)
    if not args.no_coreg:
        provider.shift = provider.estimate_shift(buildings)
        print(f"  estimated global co-registration shift (row, col): {np.round(provider.shift, 2)} px")
    df = analyse(provider, buildings, cfg)
    df = add_scores(df, cfg)
    df["class"] = classify(df["score"], cfg)

    if args.label_field:
        lab = pd.Series({b.id: b.props.get(args.label_field) for b in buildings})
        df["label"] = df["id"].map(lab)
        df["label"] = pd.to_numeric(df["label"], errors="coerce")
        if df["label"].dropna().isin([0, 1]).all() and df["label"].notna().sum() > 50:
            df, rf, imp = supervised(df, "label", cfg)
            import joblib
            joblib.dump(rf, out / "model.joblib")
            print("\nTop features:\n", imp.head(8).to_string(float_format=lambda x: f"{x:.3f}"))
            ev = evaluate(df, "label", cfg)
            print("\n", ev.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
            ev.to_csv(out / "ablation.csv", index=False)
            fig_roc(df, "label", out / "roc_ablation.png")
        else:
            print("Label field must be binary (0/1) with >50 labelled buildings; skipping supervised stage.")

    df.to_csv(out / "results.csv", index=False)
    write_geojson(df, buildings, out / "buildings.geojson")
    print("\n", df["class"].value_counts().to_string())
    top = df.sort_values("score", ascending=False)
    fig_montage(provider, buildings, df, top["id"].tolist(), "Highest-scoring buildings", out / "montage_top.png")
    mid = df[df["class"] == "review"].sort_values("score", ascending=False)
    fig_montage(provider, buildings, df, mid["id"].tolist(), "'Review' band", out / "montage_review.png")
    print(f"Wrote results to {out.resolve()}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)

    d = sp.add_parser("demo", help="run on a synthetic scene and print an ablation study")
    d.add_argument("--out", default="out_demo")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--perturb", action="store_true",
                   help="apply a random contrast/tint/brightness change to the AFTER image")
    d.set_defaults(fn=cmd_demo)

    r = sp.add_parser("run", help="run on real GeoTIFFs + footprints (needs rasterio)")
    r.add_argument("--before", required=True)
    r.add_argument("--after", required=True)
    r.add_argument("--footprints", required=True, help="GeoJSON FeatureCollection of building polygons")
    r.add_argument("--footprint-crs", default="EPSG:4326")
    r.add_argument("--label-field", default=None, help="binary 0/1 property to train/evaluate against")
    r.add_argument("--no-coreg", action="store_true")
    r.add_argument("--out", default="out_real")
    r.set_defaults(fn=cmd_run)

    def acq_args(sub):
        sub.add_argument("--place", help='place name to geocode, e.g. "Bakhmut, Ukraine"')
        sub.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"), help="lon/lat box instead of --place")
        sub.add_argument("--size-km", type=float, default=1.0, help="side of the square around --place (default 1.0)")
        sub.add_argument("--before-date", default="2022-02-24", help="imagery/footprints as of this date (default 2022-02-24)")
        sub.add_argument("--after-date", default=None, help="newest imagery allowed (default: today)")
        sub.add_argument("--zoom", type=int, default=18, help="tile zoom: 18 ~0.4 m/px at 50N, 19 ~0.2 m/px (default 18)")
        sub.add_argument("--max-tiles", type=int, default=400)
        sub.add_argument("--footprints-asof", choices=["before", "now"], default="before")
        sub.add_argument("--accept-esri-terms", action="store_true", help="skip the interactive terms prompt")
        sub.add_argument("--out", default="out_auto")

    f = sp.add_parser("fetch", help="download imagery + OSM footprints for a place (no analysis)")
    acq_args(f)
    f.set_defaults(fn=cmd_fetch)

    au = sp.add_parser("auto", help="EVERYTHING: place name in, damage triage out")
    acq_args(au)
    au.set_defaults(fn=cmd_auto)

    a = p.parse_args()
    if getattr(a, "cmd", None) in ("fetch", "auto") and not (a.place or a.bbox):
        p.error("give --place \"City, Country\" or --bbox W S E N")
    a.fn(a)


if __name__ == "__main__":
    main()
