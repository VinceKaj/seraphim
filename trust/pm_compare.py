"""Head-to-head: our P(RU by deadline) vs Polymarket price on resolved capture/enter markets.

Market -> settlement via gazetteer: among namesakes not RU-held and within 30 km of the line at market start,
the largest by population. Target per qualifier, using DeepState
signed distance at the deadline and settlement radius R (from population):
  any ("enter X"): dist > -R      capture: dist > 0      all ("all of X"): dist > +R
Model: forecast.Panel geometry features + R + h, HistGradientBoosting, re-fitted quarterly on rows whose label date
is before the cutoff (no leakage). Our p for day t uses DeepState through day t; the market price is the last trade
of day t (UTC) — the market gets up to one extra day of information.
Outputs: data/pm_compare.parquet-like npz + printed table; data/pm_compare.json (per-market Brier).
Usage: uv run trust/pm_compare.py [--text]   (--text adds the regex-claim features)
"""
import argparse
import csv
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast import GEO, TEXT, Panel, mat  # noqa: E402
from geo import DATA, TRUST_DB, norm  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402

PM_CSV = Path(__file__).resolve().parent / "research" / "pm_territorial.csv"
PM_DB = DATA / "pm.duckdb"   # Polymarket price history (table ph: cid, outcome, ts, price); not in git
HSET = [3, 7, 14, 30, 60, 120]
QUAL = {"any": -1, "capture": 0, "all": 1}
FIX = {"Dopropillia": "Dobropillia", "Druzkhivka": "Druzhkivka", "Zaporizhia": "Zaporizhzhia"}


def radius(pop):
    return np.maximum(0.7, 0.6 * np.sqrt(np.asarray(pop, float) / 1000))


def daily_prices(cids):
    import duckdb
    c = duckdb.connect(str(PM_DB), read_only=True)
    q = f"""SELECT cid, CAST(to_timestamp(ts) AT TIME ZONE 'UTC' AS DATE) d, arg_max(price, ts) p
            FROM ph WHERE outcome='Yes' AND cid IN ({",".join("'" + x + "'" for x in cids)}) GROUP BY 1, 2"""
    out = {}
    for cid, d, p in c.execute(q).fetchall():
        out.setdefault(cid, {})[d] = p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--truth", choices=["deepstate", "isw"], default="deepstate",
                    help="training label source; isw: any = ISW advance/infiltration/control, capture/all = control")
    a = ap.parse_args()
    cols = GEO + ["R"] + (TEXT if a.text else [])
    P = Panel()
    ISW = np.load(DATA / "isw_truth.npz") if a.truth == "isw" else None
    D = len(P.dates)
    d0 = P.dates[0].astype(object)
    idx = lambda d: (d - d0).days  # noqa: E731
    db = sqlite3.connect(TRUST_DB)
    names = {}
    for n, g in db.execute("SELECT norm, gid FROM name_index"):
        if g in P.col:
            names.setdefault(n, set()).add(g)
    pop = dict(db.execute("SELECT gid, pop FROM settlement").fetchall())
    Rj = radius([pop[int(g)] for g in P.gids]).astype(np.float32)

    # markets
    mk = []
    for r in csv.DictReader(open(PM_CSV)):
        q = r["qualifier"]
        if q not in QUAL or r["resolved"] not in ("Yes", "No"):
            continue
        nm = norm(FIX.get(r["settlement"], r["settlement"]))
        cands = names.get(nm) or names.get(nm.replace("ii", "i")) or set()
        if not cands:
            continue
        dl = date.fromisoformat(r["deadline"])
        mk.append(dict(cid=r["condition_id"], q=q, name=r["settlement"], cands=cands, deadline=dl,
                       y=int(r["resolved"] == "Yes"), question=r["question"], volume=float(r["volume"] or 0)))
    prices = daily_prices([m["cid"] for m in mk])
    for m in mk:
        days = sorted(d for d in prices.get(m["cid"], {}) if d < m["deadline"] and idx(d) < D)
        m["days"] = days
        t0 = idx(days[0]) if days else D - 1
        t0 = max(t0, 0)
        open_ = [g for g in m["cands"] if P.state[t0, P.col[g]] != 1 and P.dist[t0, P.col[g]] > -30] or list(m["cands"])
        m["gid"] = max(open_, key=lambda g: (pop.get(g, 0), -abs(float(P.dist[t0, P.col[g]]))))
    mk = [m for m in mk if m["days"] and idx(m["days"][0]) >= idx(date(2024, 4, 1))]
    print(f"{len(mk)} resolved markets mapped, {sum(m['y'] for m in mk)} Yes", flush=True)

    # training rows: weekly t, settlements within 25 km UA side .. 10 km RU side, all h in HSET
    weekly = np.arange(idx(date(2023, 1, 2)), D, 7)
    blocks = []
    for h in HSET:
        ts = weekly[weekly + h < D]
        tt, jj = [], []
        for t in ts:
            js = np.flatnonzero((P.dist[t] > -25) & (P.dist[t] < 10))
            tt.append(np.full(len(js), t)); jj.append(js)
        tt, jj = np.concatenate(tt), np.concatenate(jj)
        blocks.append((tt, jj, np.full(len(tt), h)))
    T_ = np.concatenate([b[0] for b in blocks]); J_ = np.concatenate([b[1] for b in blocks])
    H_ = np.concatenate([b[2] for b in blocks])

    def feats(t, j, h):
        X = {k: v[t, j] for k, v in P.f.items()}
        X["h"] = h.astype(np.float32)
        X["R"] = Rj[j]
        return mat(X, cols)

    Xall = feats(T_, J_, H_)
    cutoffs = [date(2024, 4, 1), date(2024, 7, 1), date(2024, 10, 1), date(2025, 1, 1), date(2025, 4, 1),
               date(2025, 7, 1), date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1),
               date(2026, 10, 1), date(2027, 1, 1)]
    out = []
    for q, k in QUAL.items():
        if a.truth == "isw":
            S = ISW["state"][T_ + H_, J_]
            yall = ((S >= 0) if q == "any" else (S == 1)).astype(int)
            valid = ISW["have"][T_ + H_]
        else:
            yall = (P.dist[T_ + H_, J_] > k * Rj[J_]).astype(int)
            valid = np.ones(len(T_), bool)
        ms = [m for m in mk if m["q"] == q]
        for c0, c1 in zip(cutoffs[:-1], cutoffs[1:]):
            todo = [(m, d) for m in ms for d in m["days"] if c0 <= d < c1]
            if not todo:
                continue
            tr = (T_ + H_ < idx(c0)) & valid
            mdl = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
                                                 l2_regularization=1.0, random_state=0).fit(Xall[tr], yall[tr])
            t = np.array([idx(d) for _, d in todo]); j = np.array([P.col[m["gid"]] for m, _ in todo])
            h = np.array([(m["deadline"] - d).days for m, d in todo])
            p = mdl.predict_proba(feats(t, j, np.clip(h, 1, 180)))[:, 1]
            for (m, d), pi, hi in zip(todo, p, h):
                out.append((m["cid"], q, m["name"], str(d), int(hi), float(pi), float(prices[m["cid"]][d]), m["y"]))
            print(f"  {q} fold {c0}: {len(todo)} market-days", flush=True)

    # score
    arr = np.array([(o[5], o[6], o[7]) for o in out])
    ours, mkt, y = arr[:, 0], np.clip(arr[:, 1], 0, 1), arr[:, 2]
    per = {}
    for o in out:
        per.setdefault(o[0], []).append(o)
    pm_b = [np.mean([(x[6] - x[7]) ** 2 for x in v]) for v in per.values()]
    our_b = [np.mean([(x[5] - x[7]) ** 2 for x in v]) for v in per.values()]
    wins = sum(o < m for o, m in zip(our_b, pm_b))
    print(f"\nmarket-days {len(out)}, markets {len(per)}")
    print(f"pooled Brier   ours {np.mean((ours - y) ** 2):.4f}   market {np.mean((mkt - y) ** 2):.4f}"
          f"   blend50 {np.mean(((ours + mkt) / 2 - y) ** 2):.4f}")
    print(f"per-market avg ours {np.mean(our_b):.4f}   market {np.mean(pm_b):.4f}   ours better on {wins}/{len(per)}")
    for hb in [(0, 7), (8, 30), (31, 90), (91, 999)]:
        s = (arr[:, 0] >= 0) & np.array([hb[0] <= o[4] <= hb[1] for o in out])
        if s.sum():
            print(f"  h {hb}: n={s.sum()} ours {np.mean((ours[s]-y[s])**2):.4f} market {np.mean((mkt[s]-y[s])**2):.4f}")
    tag = ("text" if a.text else "geo") + ("_isw" if a.truth == "isw" else "")
    (DATA / f"pm_compare_{tag}.json").write_text(json.dumps(
        [dict(cid=o[0], q=o[1], name=o[2], day=o[3], h=o[4], ours=round(o[5], 4), market=o[6], y=o[7]) for o in out]))


if __name__ == "__main__":
    main()
