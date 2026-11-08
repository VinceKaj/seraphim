"""Build the ISW truth matrix, aligned with the DeepState truth matrix (data/truth.npz).

Inputs (from trust/fetch_isw.py, ISW geodata, internal use only, never republish):
  data/isw/control/<date>.geojson   Assessed Russian-controlled territory (daily, 2022-02-24 -> 2026-05-31)
  data/isw/advance/<date>.geojson   Assessed Russian advances
  data/isw/infil/<date>.geojson     Assessed Russian infiltration areas (from 2025-11)
  data/isw/webmap/*.json            live web map + Wayback copies (Esri JSON, Web Mercator, embedded features)
  data/isw/pre2022.geojson          Russian-controlled territory before 2022-02-24 (added to control every day)
  data/truth.npz                    dates[D], gids[N], dist[D,N], state[D,N]  (DeepState)
  trust.db settlement(gid, lat, lon)
Output: data/isw_truth.npz
  dates[D]   same daily calendar as truth.npz
  gids[N]    same order as truth.npz
  state[D,N] int8: 1 = inside ISW assessed Russian control, 0 = inside assessed advance / infiltration
             (and not control), -1 = neither. Forward-filled across days without a snapshot.
  have[D]    bool: True only on days with an actual ISW snapshot.
Usage: uv run trust/build_isw_truth.py
"""
import collections
import gzip
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon, shape

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ISW = DATA / "isw"
R = 6378137.0
WEBMAP_KIND = {
    "Assessed Russian-Controlled Terrain": "control",
    "Russian-controlled Ukrainian Territory Before Feb 24, 2022": "control",
    "Assessed Russian Advances": "advance",
    "Assessed Russian Infiltration Areas": "infil",
}


BAD = collections.Counter()


def prep(g):
    try:
        g = shapely.make_valid(g)
    except shapely.errors.GEOSException:
        try:
            g = g.buffer(0)
        except shapely.errors.GEOSException:
            BAD["dropped"] += 1
            return None
        BAD["buffer0"] += 1
    shapely.prepare(g)
    return g


def geojson_feats(path: Path) -> list:
    """Each feature -> [geometry] (inside = contained by the geometry)."""
    d = json.loads(path.read_text())
    gs = [prep(shape(f["geometry"])) for f in d.get("features", []) if f.get("geometry")]
    return [[g] for g in gs if g is not None]


def merc_to_ll(ring):
    a = np.asarray(ring, float)[:, :2]
    if np.abs(a).max() <= 180:  # already lon/lat (wkid 4326): the live web map stores it this way
        return a
    lon = a[:, 0] / R * 180 / math.pi
    lat = (2 * np.arctan(np.exp(a[:, 1] / R)) - math.pi / 2) * 180 / math.pi
    return np.c_[lon, lat]


def read_json(path: Path):
    b = path.read_bytes()
    return json.loads(gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)  # Wayback copies are gzip


def webmap_snapshots(path: Path) -> dict:
    """Esri JSON feature collections -> {date: {kind: [feature]}}; feature = list of ring polygons (even-odd)."""
    d = read_json(path)
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for lyr in d.get("operationalLayers", []):
        kind = WEBMAP_KIND.get(lyr.get("title"))
        fc = lyr.get("featureCollection")
        if not kind or not fc:
            continue
        for sub in fc.get("layers", []):
            for f in sub.get("featureSet", {}).get("features", []):
                rings = (f.get("geometry") or {}).get("rings") or []
                polys = [g for g in (prep(Polygon(merc_to_ll(r))) for r in rings if len(r) >= 4) if g is not None]
                day = (f.get("attributes") or {}).get("pub_date")
                if polys:
                    out[day][kind].append(polys)
    # the pre-2022 layer has no pub_date: attach it to every dated snapshot
    pre = out.pop(None, {})
    days = {k: v for k, v in out.items() if k}
    for v in days.values():
        for kind, feats in pre.items():
            v[kind] += feats
    return days


def inside(feats: list, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    m = np.zeros(len(x), bool)
    for parts in feats:
        if len(parts) == 1:
            m |= shapely.contains_xy(parts[0], x, y)
        else:  # Esri rings: even-odd rule (holes are rings inside outer rings)
            cnt = np.zeros(len(x), np.int32)
            for p in parts:
                cnt += shapely.contains_xy(p, x, y)
            m |= (cnt % 2) == 1
    return m


def main():
    t = np.load(DATA / "truth.npz")
    dates, gids, ds_state, ds_dist = t["dates"], t["gids"], t["state"], t["dist"]
    D, N = len(dates), len(gids)
    con = sqlite3.connect(f"file:{ROOT / 'trust.db'}?mode=ro", uri=True)
    ll = dict((g, (lo, la)) for g, la, lo in con.execute("select gid, lat, lon from settlement"))
    x = np.array([ll[int(g)][0] for g in gids])
    y = np.array([ll[int(g)][1] for g in gids])

    pre = geojson_feats(ISW / "pre2022.geojson") if (ISW / "pre2022.geojson").exists() else []
    first, last = str(dates[0]), str(dates[-1])

    snaps = {}  # date -> state[N]
    src = collections.Counter()
    ctrl_days = sorted(p.stem for p in (ISW / "control").glob("*.geojson"))
    # keep the last snapshot before the calendar start, for forward-fill
    before = [d for d in ctrl_days if d < first]
    keep = ([before[-1]] if before else []) + [d for d in ctrl_days if first <= d <= last]
    for day in keep:
        ctrl = geojson_feats(ISW / "control" / f"{day}.geojson")
        if not ctrl:
            continue  # no ISW control polygons that day = no snapshot
        zone = []
        for k in ("advance", "infil"):
            p = ISW / k / f"{day}.geojson"
            if p.exists():
                zone += geojson_feats(p)
        c = inside(ctrl + pre, x, y)
        z = inside(zone, x, y) if zone else np.zeros(N, bool)
        snaps[day] = np.where(c, 1, np.where(z, 0, -1)).astype(np.int8)
        src["daily"] += 1
    for p in sorted((ISW / "webmap").glob("*.json")):
        for day, kinds in webmap_snapshots(p).items():
            if day in snaps or not (first <= day <= last) or not kinds.get("control"):
                continue
            c = inside(kinds["control"], x, y)
            z = inside(kinds.get("advance", []) + kinds.get("infil", []), x, y)
            snaps[day] = np.where(c, 1, np.where(z, 0, -1)).astype(np.int8)
            src["webmap"] += 1
            print(f"webmap snapshot {day} from {p.name}")

    state = np.full((D, N), -1, np.int8)
    have = np.zeros(D, bool)
    cur = None
    s_iter = sorted(snaps)
    j = 0
    for i, d in enumerate(dates.astype(str)):
        while j < len(s_iter) and s_iter[j] <= d:
            cur = snaps[s_iter[j]]
            j += 1
        if d in snaps:
            have[i] = True
        if cur is not None:
            state[i] = cur
    np.savez_compressed(DATA / "isw_truth.npz", dates=dates, gids=gids, state=state, have=have)

    def counts(row):
        return {int(k): int(v) for k, v in zip(*np.unique(row, return_counts=True))}

    print(f"geometry repairs: {dict(BAD)}")
    print(f"snapshots: {dict(src)}; days with have=True: {have.sum()} of {D} ({first} -> {last})")
    gap = [str(d) for d, h in zip(dates, have) if not h]
    print(f"days without snapshot: {len(gap)}; first/last: {gap[:3]} ... {gap[-3:]}")
    for d in ("2025-06-01", last):
        i = int(np.where(dates.astype(str) == d)[0][0])
        print(f"{d} have={have[i]} ISW {counts(state[i])}  DeepState {counts(ds_state[i])}")
    near = (np.abs(ds_dist) < 10) & have[:, None]
    isw_ru, ds_ru = state == 1, ds_state == 1
    agree = (isw_ru == ds_ru)[near].mean()
    firm = near & (ds_state != 0)
    agree_firm = (isw_ru == ds_ru)[firm].mean()
    print(f"agreement within 10 km of DS line, have days: {agree:.3f} (n={near.sum()}); "
          f"excluding DS contested: {agree_firm:.3f} (n={firm.sum()})")
    tab = collections.Counter(zip(state[near].tolist(), ds_state[near].tolist()))
    print("confusion (ISW, DS): count ->", {k: tab[k] for k in sorted(tab)})
    by_year = collections.defaultdict(list)
    for i, d in enumerate(dates.astype(str)):
        if have[i]:
            m = near[i]
            if m.any():
                by_year[d[:4]].append(((isw_ru[i] == ds_ru[i])[m]).mean())
    print("agreement by year:", {k: round(float(np.mean(v)), 3) for k, v in sorted(by_year.items())})


if __name__ == "__main__":
    main()
