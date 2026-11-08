"""P(settlement RU-held at t+h) — walk-forward backtest: geometry-only vs geometry + text claims.

Rows: weekly as-of days t, settlements not RU-held at t and within 25 km on the UA side of the line, h in HORIZONS.
Label: DeepState state[t+h] == 1. All features use data up to day t only.
  geo:  dist, d_dist30 (movement), RU gains within 10 km in last 7/30 days, contested flag, h
  text: claims (regex, RU channels) about this settlement in last 30 d, days since last claim, mod_russia in last 30 d,
        claims within 10 km in last 30 d
Folds: quarterly test windows; a fold trains only on rows with t + h < fold start (label known before the fold).
Usage: uv run trust/forecast.py            -> prints metrics table, writes data/backtest.json
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import DATA, TRUST_DB, proj  # noqa: E402

HORIZONS = [7, 30, 90]
NEAR_KM, ZONE_KM = 10.0, 25.0
GEO = ["dist", "d_dist30", "gain7", "gain30", "contested", "h"]
TEXT = ["claim30", "days_since_claim", "mod30", "near_claims30"]


def shift(a, k, fill):
    out = np.full_like(a, fill)
    out[k:] = a[:-k]
    return out


def roll(a, w):
    c = np.cumsum(a, axis=0, dtype=np.float32)
    return c - shift(c, w, 0)


class Panel:
    def __init__(self, claim_table="claim_regex"):
        T = np.load(DATA / "truth.npz")
        self.dates, self.gids = T["dates"], T["gids"]
        self.state, self.dist = T["state"], T["dist"]
        self.col = {int(g): j for j, g in enumerate(self.gids)}
        db = sqlite3.connect(TRUST_DB)
        ll = dict((g, (lat, lon)) for g, lat, lon in db.execute("SELECT gid, lat, lon FROM settlement"))
        lat = np.array([ll[int(g)][0] for g in self.gids])
        lon = np.array([ll[int(g)][1] for g in self.gids])
        self.lat, self.lon = lat, lon
        x, y = proj(lon, lat)
        pairs = cKDTree(np.c_[x, y]).query_pairs(NEAR_KM, output_type="ndarray")
        N = len(self.gids)
        A = sparse.coo_matrix((np.ones(len(pairs) * 2, np.float32),
                               (np.r_[pairs[:, 0], pairs[:, 1]], np.r_[pairs[:, 1], pairs[:, 0]])), shape=(N, N)).tocsr()
        s, d = self.state, self.dist
        ru = (s == 1).astype(np.float32)
        f = {}
        f["dist"] = d
        f["d_dist30"] = d - shift(d, 30, np.nan)
        for w in (7, 30):
            flip = ru * (1 - shift(ru, w, 1))
            f[f"gain{w}"] = np.asarray(A.T.dot(flip.T).T, dtype=np.float32)
        f["contested"] = (s == 0).astype(np.float32)
        # text features
        D = len(self.dates)
        d0 = self.dates[0].astype(object)
        C = np.zeros((D, N), np.float32)
        M = np.zeros((D, N), np.float32)
        for ch, g, pa in db.execute(f"SELECT DISTINCT channel, gid, substr(posted_at,1,10) FROM {claim_table} "
                                    "WHERE in_scope=1"):
            t = (date.fromisoformat(pa) - d0).days
            if g in self.col and 0 <= t < D:
                C[t, self.col[g]] += 1
                M[t, self.col[g]] += ch == "mod_russia"
        f["claim30"] = roll(C, 30)
        f["mod30"] = roll(M, 30)
        f["near_claims30"] = np.asarray(A.T.dot((roll(C, 30) > 0).astype(np.float32).T).T, dtype=np.float32)
        last = np.where(C > 0, np.arange(D)[:, None], -10_000)
        last = np.maximum.accumulate(last, axis=0)
        f["days_since_claim"] = np.minimum(np.arange(D)[:, None] - last, 365).astype(np.float32)
        self.f = f

    def rows(self, ts, h, gidx=None):
        """Feature matrix for as-of day indices ts (array) and horizon h; eligible settlements only."""
        out_t, out_j = [], []
        for t in ts:
            js = np.flatnonzero((self.state[t] != 1) & (self.dist[t] > -ZONE_KM)) if gidx is None else gidx
            out_t.append(np.full(len(js), t))
            out_j.append(js)
        t, j = np.concatenate(out_t), np.concatenate(out_j)
        X = {k: (v[t, j] if k != "h" else None) for k, v in self.f.items()}
        X["h"] = np.full(len(t), h, np.float32)
        return t, j, X

    def label(self, t, j, h):
        return (self.state[t + h, j] == 1).astype(int)


def mat(X, cols):
    return np.column_stack([X[c] for c in cols]).astype(np.float32)


def fit(Xtr, ytr, cols):
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
                                       random_state=0)
    return m.fit(mat(Xtr, cols), ytr)


def metrics(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return dict(n=int(len(y)), pos=int(y.sum()), brier=round(float(brier_score_loss(y, p)), 5),
                logloss=round(float(log_loss(y, p, labels=[0, 1])), 4),
                auc=round(float(roc_auc_score(y, p)), 4) if 0 < y.sum() < len(y) else None)


def main():
    P = Panel()
    D = len(P.dates)
    d0 = P.dates[0].astype(object)
    idx = lambda s: (date.fromisoformat(s) - d0).days  # noqa: E731
    weekly = np.arange(idx("2023-01-02"), D, 7)
    folds = [(idx(a), idx(b)) for a, b in [("2024-01-01", "2024-04-01"), ("2024-04-01", "2024-07-01"),
             ("2024-07-01", "2024-10-01"), ("2024-10-01", "2025-01-01"), ("2025-01-01", "2025-04-01"),
             ("2025-04-01", "2025-07-01"), ("2025-07-01", "2025-10-01"), ("2025-10-01", "2026-01-01"),
             ("2026-01-01", "2026-05-01"), ("2026-05-01", "2026-11-08")]]
    res = {}
    for h in HORIZONS:
        t_all, j_all, X_all = P.rows(weekly[weekly + h < D], h)
        y_all = P.label(t_all, j_all, h)
        preds = {k: np.full(len(y_all), np.nan) for k in ("base", "geo", "geo+text")}
        for a, b in folds:
            tr = t_all + h < a
            te = (t_all >= a) & (t_all < b)
            if te.sum() == 0 or tr.sum() == 0:
                continue
            Xtr = {k: v[tr] for k, v in X_all.items()}
            Xte = {k: v[te] for k, v in X_all.items()}
            preds["base"][te] = y_all[tr].mean()
            preds["geo"][te] = fit(Xtr, y_all[tr], GEO).predict_proba(mat(Xte, GEO))[:, 1]
            preds["geo+text"][te] = fit(Xtr, y_all[tr], GEO + TEXT).predict_proba(mat(Xte, GEO + TEXT))[:, 1]
        ok = ~np.isnan(preds["geo"])
        locked = ok & (t_all >= folds[-1][0])
        claimed = ok & (X_all["claim30"] > 0)
        res[h] = {}
        for name, mask in (("all_folds", ok), ("locked_2026-05+", locked), ("claimed_last30d", claimed)):
            res[h][name] = {k: metrics(y_all[mask], v[mask]) for k, v in preds.items()}
        np.savez_compressed(DATA / f"backtest_h{h}.npz", t=t_all[ok], j=j_all[ok], y=y_all[ok],
                            **{k.replace("+", "_"): v[ok] for k, v in preds.items()})
        for name, r in res[h].items():
            print(f"h={h:3d} {name:18s} " + " | ".join(
                f"{k}: brier {m['brier']:.4f} ll {m['logloss']:.3f} auc {m['auc']}" for k, m in r.items())
                + f"  (n={r['geo']['n']}, pos={r['geo']['pos']})", flush=True)
    (DATA / "backtest.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
