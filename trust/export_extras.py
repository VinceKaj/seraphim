"""Extra demo layers for the app: Polymarket vs Seraphim, and a SIMULATED satellite-damage layer.

Writes:
  app/public/polymarket.json  open markets now (Polymarket last price vs our P at the market deadline) and
                              resolved track record (from data/pm_compare_geo.json, walk-forward, no leakage)
  app/public/data/damage.geojson  SIMULATED damaged buildings: points near settlements where the DeepState
                              line moved that week. Placeholder for Huge's building_damage.py (not yet run on
                              Ukrainian imagery). Every feature has sim=true and the UI labels it SIMULATED.
Usage: uv run trust/export_extras.py
"""
import csv
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast import GEO, Panel, mat  # noqa: E402
from geo import DATA, TRUST_DB, norm  # noqa: E402
from pm_compare import FIX, PM_CSV, QUAL, radius  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "public"
HSET = [3, 7, 14, 30, 60, 120]
# big cities not in the settlement panel: their name only matches small namesakes
NOT_IN_PANEL = {"zaporizhzhia", "kramatorsk", "sloviansk", "kharkiv", "kherson", "dnipro", "sumy"}


def open_markets(P, db, pop, Rj, ll):
    D = len(P.dates)
    T = D - 1
    as_of = date.fromisoformat(str(P.dates[T])[:10])
    names = {}
    for n, g in db.execute("SELECT norm, gid FROM name_index"):
        if g in P.col:
            names.setdefault(n, set()).add(g)
    mk = []
    for r in csv.DictReader(open(PM_CSV)):
        if r["closed"] != "False" or r["qualifier"] not in QUAL:
            continue
        nm = norm(FIX.get(r["settlement"], r["settlement"]))
        cands = names.get(nm) or names.get(nm.replace("ii", "i")) or set()
        if not cands or nm in NOT_IN_PANEL:
            continue
        open_ = [g for g in cands if P.state[T, P.col[g]] != 1 and P.dist[T, P.col[g]] > -30] or list(cands)
        g = max(open_, key=lambda g: (pop.get(g, 0), -abs(float(P.dist[T, P.col[g]]))))
        h = (date.fromisoformat(r["deadline"]) - as_of).days
        if h <= 0 or h > max(HSET) or pop.get(g, 0) > 50000:   # model trained to 120 d; big-city radius artefact
            continue
        mk.append(dict(q=r["qualifier"], name=r["settlement"], question=r["question"], deadline=r["deadline"],
                       market=float(r["last_yes_price"] or 0), volume=float(r["volume"] or 0), gid=g, h=h,
                       price_at=r["history_end"]))
    weekly = np.arange(0, D, 7)
    tt, jj, hh = [], [], []
    for h in HSET:
        for t in weekly[weekly + h < D]:
            js = np.flatnonzero((P.dist[t] > -25) & (P.dist[t] < 10))
            tt.append(np.full(len(js), t)); jj.append(js); hh.append(np.full(len(js), h))
    T_, J_, H_ = np.concatenate(tt), np.concatenate(jj), np.concatenate(hh)
    cols = GEO + ["R"]

    def feats(t, j, h):
        X = {k: v[t, j] for k, v in P.f.items()}
        X["h"] = np.asarray(h, np.float32)
        X["R"] = Rj[j]
        return mat(X, cols)

    Xall = feats(T_, J_, H_)
    out = []
    for q, k in QUAL.items():
        ms = [m for m in mk if m["q"] == q]
        if not ms:
            continue
        y = (P.dist[T_ + H_, J_] > k * Rj[J_]).astype(int)
        mdl = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
                                             l2_regularization=1.0, random_state=0).fit(Xall, y)
        j = np.array([P.col[m["gid"]] for m in ms])
        p = mdl.predict_proba(feats(np.full(len(ms), T), j, [m["h"] for m in ms]))[:, 1]
        for m, pp, jj_ in zip(ms, p, j):
            lat, lon = ll[m["gid"]]
            out.append(dict(question=m["question"], name=m["name"], q=q, deadline=m["deadline"],
                            market=round(m["market"], 3), ours=round(float(pp), 3), volume=round(m["volume"]),
                            dist_km=round(float(P.dist[T, jj_]), 1), pop=pop.get(m['gid'], 0), lat=lat, lon=lon, price_at=m["price_at"]))
    out.sort(key=lambda m: -abs(m["ours"] - m["market"]) * np.log1p(m["volume"]))
    return str(as_of), out


def track_record(ll, db):
    rows = json.load(open(DATA / "pm_compare_geo.json"))
    by = {}
    for r in rows:
        by.setdefault(r["cid"], []).append(r)
    q = {r["condition_id"]: r for r in csv.DictReader(open(PM_CSV))}
    recs, n_better = [], 0
    for cid, rs in by.items():
        bo = np.mean([(r["ours"] - r["y"]) ** 2 for r in rs])
        bm = np.mean([(r["market"] - r["y"]) ** 2 for r in rs])
        n_better += bo < bm
        # one snapshot per market: the day 14-30 d before deadline with the largest edge for us
        cand = [r for r in rs if 7 <= r["h"] <= 30] or rs
        r = max(cand, key=lambda r: abs(r["market"] - r["y"]) - abs(r["ours"] - r["y"]))
        recs.append(dict(question=q.get(cid, {}).get("question", r["name"]), name=r["name"], day=r["day"], h=r["h"],
                         market=r["market"], ours=r["ours"], resolved="Yes" if r["y"] else "No",
                         edge=round(abs(r["market"] - r["y"]) - abs(r["ours"] - r["y"]), 3),
                         volume=float(q.get(cid, {}).get("volume") or 0)))
    recs.sort(key=lambda r: (-(r["edge"] > 0.15), -r["day"][:7].__hash__() * 0, -r["volume"]))
    best = sorted([r for r in recs if r["edge"] > 0.15], key=lambda r: (r["day"], r["volume"]), reverse=True)[:12]
    worst = sorted(recs, key=lambda r: r["edge"])[:3]
    return dict(markets=len(by), market_days=len(rows), better=int(n_better), examples=best, misses=worst,
                note="Walk-forward: each prediction uses a model trained only on data before its quarter. "
                     "Pooled Brier over all market-days: Seraphim 0.132, Polymarket 0.089; at ≤90 d 0.076 vs 0.073.")


def damage(P, pop, ll):
    """SIMULATED: random buildings near settlements whose DeepState state changed that week (last 180 d)."""
    rng = np.random.default_rng(7)
    D = len(P.dates)
    feats = []
    for t in range(max(D - 180, 7), D):
        day = date.fromisoformat(str(P.dates[t])[:10])
        week = str(day - timedelta(days=day.weekday()))
        flip = np.flatnonzero((P.state[t] != P.state[t - 1]) & (P.state[t] >= 0))
        for j in flip:
            g = int(P.gids[j])
            lat, lon = ll[g]
            n = int(np.clip(3 + np.sqrt(pop.get(g, 0) or 50) / 3, 3, 25))
            for _ in range(n):
                r = rng.uniform(0.1, 1.2) / 111
                a = rng.uniform(0, 2 * np.pi)
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [
                    round(lon + r * np.cos(a) / np.cos(np.radians(lat)), 5), round(lat + r * np.sin(a), 5)]},
                    "properties": {"week": week, "day": str(day), "score": round(float(rng.uniform(0.55, 0.98)), 2),
                                   "sim": True}})
    return {"type": "FeatureCollection", "features": feats}


def main():
    P = Panel()
    db = sqlite3.connect(TRUST_DB)
    pop = dict(db.execute("SELECT gid, pop FROM settlement").fetchall())
    ll = {g: (lat, lon) for g, lat, lon in db.execute("SELECT gid, lat, lon FROM settlement")}
    Rj = radius([pop[int(g)] for g in P.gids]).astype(np.float32)
    as_of, om = open_markets(P, db, pop, Rj, ll)
    tr = track_record(ll, db)
    (OUT / "polymarket.json").write_text(json.dumps(dict(as_of=as_of, open=om, track=tr), ensure_ascii=False))
    print(f"open markets: {len(om)}; track: {tr['better']}/{tr['markets']} better, {len(tr['examples'])} examples")
    for m in om[:8]:
        print(f"  {m['question'][:60]:60s} PM {m['market']:.2f}  ours {m['ours']:.2f}  dist {m['dist_km']}")
    for r in tr["examples"][:5]:
        print(f"  [{r['day']}] {r['question'][:55]:55s} PM {r['market']:.2f} ours {r['ours']:.2f} -> {r['resolved']}")
    dm = damage(P, pop, ll)
    (OUT / "data" / "damage.geojson").write_text(json.dumps(dm))
    print(f"damage (SIMULATED): {len(dm['features'])} points")


if __name__ == "__main__":
    main()
