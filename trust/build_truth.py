"""Build the gazetteer of front-zone settlements and the DeepState truth matrix.

Inputs (read-only): GeoNames UA/RU dumps, Kacper's daily DeepState polygons, DeepState raw cache (grey zone).
Outputs:
  trust.db: settlement(gid, name, name_ru, name_uk, lat, lon, admin1, pop, h3_7, in_scope)
            name_index(norm, gid, lang)        -- every spelling -> settlement
            transition(gid, date, prev, state)  -- state changes over time
  data/truth.npz: dates[D], gids[N], dist[D,N] signed km to the line (+ = inside RU-held), state[D,N] int8
                  (1 = ru, 0 = contested, -1 = ua). Days without a DeepState file are forward-filled.

Line = front part of the RU-held boundary only (boundary points with a UA-held settlement within FRONT_KM).
Contested = inside DeepState's "Unknown status" polygon (exists only up to 2024-07-07),
            or |dist| < BAND_KM (line-drawing uncertainty; tunable).
Usage: uv run trust/build_truth.py --geonames <dir>
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import h3
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import DATA, DS_RAW, FRONTLINE, TRUST_DB, norm, proj, proj_xy  # noqa: E402

BAND_KM = 0.5
ZONE_KM = 40.0
SEG_KM = 0.2
FRONT_KM = 8.0
RU_ADMIN = {"41", "09", "10"}   # Kursk, Belgorod, Bryansk: kept for name resolution, out of scope for truth


def load_places(gdir: Path):
    places = {}
    for cc, fn in (("UA", "UA.txt"), ("RU", "RU.txt")):
        for line in open(gdir / fn, encoding="utf-8"):
            c = line.rstrip("\n").split("\t")
            if c[6] != "P" or (cc == "RU" and c[10] not in RU_ADMIN):
                continue
            places[int(c[0])] = dict(gid=int(c[0]), name=c[1], ascii=c[2], alts=c[3], lat=float(c[4]),
                                     lon=float(c[5]), cc=cc, admin1=c[10], pop=int(c[14] or 0))
    return places


def load_altnames(gdir: Path, gids: set):
    alt = {}
    for line in open(gdir / "alt" / "UA.txt", encoding="utf-8"):
        c = line.rstrip("\n").split("\t")
        g = int(c[1])
        if g in gids and c[2] in ("ru", "uk", "en", "", "crh"):
            alt.setdefault(g, []).append((c[2], c[3], c[4] == "1"))
    return alt


def front_tree(geom_xy, outside_pts):
    """KD-tree over the *front* part of the RU-held boundary: boundary points with a UA-held settlement
    within FRONT_KM. This drops the coast and the Russian state border."""
    b = shapely.get_coordinates(shapely.segmentize(geom_xy.boundary, SEG_KM))
    d, _ = cKDTree(outside_pts).query(b, distance_upper_bound=FRONT_KM, workers=-1)
    return cKDTree(b[np.isfinite(d)])


def to_xy(geom):
    return shapely.transform(geom, proj_xy)


def load_day(path: Path):
    d = json.loads(path.read_text())
    geoms = [shape(f["geometry"]) for f in d["features"]]
    ver = d["features"][0]["properties"].get("version") if d["features"] else None
    return to_xy(shapely.union_all(geoms)), ver


def grey_zone(ver):
    if ver is None:
        return None
    p = DS_RAW / f"{ver}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    g = [shape(f["geometry"]) for f in d["features"]
         if f["geometry"]["type"] in ("Polygon", "MultiPolygon")
         and "unknown" in (f["properties"].get("name") or "").lower()]
    return to_xy(shapely.union_all(g)) if g else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geonames", required=True, type=Path)
    a = ap.parse_args()

    places = load_places(a.geonames)
    ua = [p for p in places.values() if p["cc"] == "UA"]
    x, y = proj([p["lon"] for p in ua], [p["lat"] for p in ua])
    pts = np.c_[x, y]
    files = sorted(FRONTLINE.glob("*.geojson"))
    fdates = [date.fromisoformat(f.stem) for f in files]
    print(f"{len(ua)} UA places, {len(files)} DeepState days {fdates[0]} -> {fdates[-1]}", flush=True)

    # pass 1: signed distance + state for all UA places on every DeepState day
    D, N = len(files), len(ua)
    dist = np.empty((D, N), np.float32)
    grey = np.zeros((D, N), bool)
    for i, f in enumerate(files):
        g, ver = load_day(f)
        inside = shapely.contains_xy(g, x, y)
        d, _ = front_tree(g, pts[~inside]).query(pts, workers=-1)
        dist[i] = np.where(inside, d, -d)
        gz = grey_zone(ver)
        if gz is not None:
            grey[i] = shapely.contains_xy(gz, x, y)
        if i % 100 == 0:
            print(f"  {f.stem}", flush=True)

    keep = np.abs(dist).min(axis=0) < ZONE_KM
    print(f"front zone (<{ZONE_KM} km of line on any day): {keep.sum()} settlements", flush=True)
    dist, grey = dist[:, keep], grey[:, keep]
    zone = [p for p, k in zip(ua, keep) if k]

    # forward-fill onto a full daily calendar
    cal = [fdates[0] + timedelta(n) for n in range((fdates[-1] - fdates[0]).days + 1)]
    idx = np.searchsorted(np.array(fdates, dtype="datetime64[D]"), np.array(cal, dtype="datetime64[D]"), side="right") - 1
    dist, grey = dist[idx], grey[idx]
    state = np.where(dist > 0, 1, -1).astype(np.int8)
    state[(np.abs(dist) < BAND_KM) | grey] = 0
    gids = np.array([p["gid"] for p in zone])
    np.savez_compressed(DATA / "truth.npz", dates=np.array(cal, dtype="datetime64[D]"), gids=gids,
                        dist=dist, state=state, band_km=BAND_KM)

    # gazetteer tables
    zone_g = {p["gid"] for p in zone}
    ru_places = [p for p in places.values() if p["cc"] == "RU"]
    alt = load_altnames(a.geonames, zone_g)
    db = sqlite3.connect(TRUST_DB)
    db.executescript("""
    DROP TABLE IF EXISTS settlement; DROP TABLE IF EXISTS name_index; DROP TABLE IF EXISTS transition;
    CREATE TABLE settlement(gid INTEGER PRIMARY KEY, name TEXT, name_ru TEXT, name_uk TEXT, lat REAL, lon REAL,
                            cc TEXT, admin1 TEXT, pop INTEGER, h3_7 TEXT, in_scope INTEGER);
    CREATE TABLE name_index(norm TEXT, gid INTEGER, lang TEXT, PRIMARY KEY(norm, gid));
    CREATE TABLE transition(gid INTEGER, date TEXT, prev INTEGER, state INTEGER);
    CREATE INDEX ix_tr ON transition(gid, date);
    """)
    rows, names = [], []
    for p in zone + ru_places:
        g = p["gid"]
        a_ = alt.get(g, [])
        pick = lambda lang: next((n for l, n, pref in sorted(a_, key=lambda t: not t[2]) if l == lang), None)  # noqa: E731
        rows.append((g, p["name"], pick("ru"), pick("uk"), p["lat"], p["lon"], p["cc"], p["admin1"], p["pop"],
                     h3.latlng_to_cell(p["lat"], p["lon"], 7), int(g in zone_g)))
        variants = {(norm(p["name"]), "en"), (norm(p["ascii"]), "en")}
        variants |= {(norm(n), "?") for n in p["alts"].split(",") if n}
        variants |= {(norm(n), l or "?") for l, n, _ in a_}
        names += [(n, g, l) for n, l in variants if len(n) >= 3]
    db.executemany("INSERT INTO settlement VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT OR IGNORE INTO name_index VALUES (?,?,?)", names)
    tr = []
    for j, g in enumerate(gids):
        s = state[:, j]
        ch = np.flatnonzero(np.diff(s)) + 1
        tr += [(int(g), str(cal[k]), int(s[k - 1]), int(s[k])) for k in ch]
    db.executemany("INSERT INTO transition VALUES (?,?,?,?)", tr)
    db.commit()
    print(f"settlement rows {len(rows)} (in scope {len(zone)}), names {len(names)}, transitions {len(tr)}")
    print("state today:", {k: int(v) for k, v in zip(*np.unique(state[-1], return_counts=True))})


if __name__ == "__main__":
    main()
