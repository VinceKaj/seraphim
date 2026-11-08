"""Export demo data for the map app (seraphim/app/, built on Kacper's map code).

Writes:
  app/public/events.geojson           one event per extracted LLM claim (v5) of the last 30 days, with credibility v0
                                       as trust-factor (source trust in stats.json uses ALL v5 claims)
  app/public/data/h3/predictions.json  P(RU control by +7/+30/+90 d) per H3 res-6 cell (max over its settlements)
  app/public/stats.json                scale numbers for the pitch
Credibility v0 (no trust model yet): DeepState outcome at the latest truth date when the claim is checkable
(confirmed 0.9 / contradicted 0.15 after >= 7 d), else corroboration (independent sources, opposing side).
Usage: uv run trust/export_demo.py
"""
import collections
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import h3
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast import GEO, HORIZONS, Panel, fit, mat  # noqa: E402
from geo import TRUST_DB  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "public"
RECORDS = ROOT / "record-store" / "records.db"
TYPE = {"captured": "capture", "entered": "movement", "advanced_near": "movement", "attack_repelled": "attack",
        "holds": "statement", "withdrew": "movement"}
RU_GAIN = {"ru_captured", "ru_entered", "ua_withdrew"}
UA_GAIN = {"ua_captured", "ua_entered", "ru_withdrew"}


def main():
    P = Panel()
    D = len(P.dates)
    T = D - 1
    as_of = str(P.dates[T])[:10]
    d0 = date.fromisoformat(str(P.dates[0])[:10])
    db = sqlite3.connect(TRUST_DB)
    rec = sqlite3.connect(f"file:{RECORDS}?mode=ro", uri=True)
    side = dict(db.execute("SELECT author, side FROM source_side"))
    st = {g: (n, lat, lon) for g, n, lat, lon in db.execute("SELECT gid, name, lat, lon FROM settlement")}
    rows = db.execute("SELECT c.record_id, c.n, c.source, c.posted_at, c.gid, c.name, c.state, c.modality, "
                      "c.attributed_to, c.span, c.landmark FROM claim_llm c WHERE c.version='v5' AND c.gid IS NOT NULL"
                      ).fetchall()
    # corroboration: distinct sources per (gid, state, day)
    by = collections.defaultdict(set)
    for r in rows:
        by[(r[4], r[6], r[3][:10])].add(r[2])

    def corro(g, s, day):
        d = date.fromisoformat(day)
        srcs = set()
        for k in range(-3, 4):
            srcs |= by.get((g, s, str(d + timedelta(days=k))), set())
        return srcs

    def outcome(g, s, day):
        j = P.col.get(g)
        t = (date.fromisoformat(day) - d0).days
        if j is None or s not in RU_GAIN | UA_GAIN or t > T:
            return "n/a"
        # outcome = DeepState in the 90 days after the claim (not today's map: no hindsight from later fighting).
        # DeepState often lags claims by weeks, so a claim younger than 90 d that is not yet shown stays pending.
        agree = (lambda a: a >= 0) if s in RU_GAIN else (lambda a: a <= 0)
        if t >= 1 and agree(P.state[t - 1, j]):
            return "echo"          # DeepState already showed it: repeats a known fact, not counted
        win = P.state[max(t, 0):min(t + 90, T) + 1, j]
        ok = bool(agree(win).any())
        if ok:
            return "confirmed"
        return "contradicted" if T - t >= 90 else "pending"

    urls = {}
    ids = list({r[0] for r in rows})
    for i in range(0, len(ids), 900):
        q = ",".join("?" * len(ids[i:i + 900]))
        for rid, u in rec.execute(f"SELECT id, json_extract(data,'$.url') FROM records WHERE id IN ({q})", ids[i:i + 900]):
            urls[rid] = u
    cutoff = str(date.fromisoformat(as_of) - timedelta(days=30))
    feats, src_rec = [], collections.defaultdict(lambda: [0, 0, 0])
    for rid, n, src, pa, g, name, s, mod, attr, span, lm in rows:
        day = pa[:10]
        srcs = corro(g, s, day)
        sides = {side.get(x) for x in srcs}
        actor = "ru" if s.startswith("ru") else "ua"
        own = side.get(src)
        opp = any(x and x in ("ru", "ua") and x != actor for x in sides) and own != actor
        out = outcome(g, s, day)
        if out == "confirmed":
            tr = 0.9
        elif out == "contradicted":
            tr = 0.15
        else:
            tr = min(0.45 + 0.07 * min(len(srcs) - 1, 4) + (0.15 if opp else 0), 0.8)
        if mod in ("denied", "unconfirmed"):
            tr = max(tr - 0.1, 0.05)
        sr = src_rec[src]
        sr[0] += out == "confirmed"
        sr[1] += out == "contradicted"
        sr[2] += 1
        if pa < cutoff:  # map shows only the last 30 days; source trust above uses every claim
            continue
        nm, lat, lon = st[g]
        verb = s[3:].replace("_", " ")
        who = {"ru": "Russia", "ua": "Ukraine"}[actor]
        desc = (f"“{span}”\n\n{src.split(':', 1)[1]} ({own or 'unknown side'}) · {mod}"
                + (f" · attributed to {attr}" if attr else "") + (f" · landmark: {lm}" if lm else "")
                + f"\n\nDeepState check: {out} · corroborated by {len(srcs)} source(s)"
                + (" incl. the opposing side" if opp else "") + f"\n\nRecord {rid} · extractor v5")
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                      "properties": {"id": f"{rid}-{n}", "name": f"{who}: {verb} — {nm}", "time": pa,
                                     "location": nm, "side": {"ru": "russia", "ua": "ukraine"}[actor],
                                     "type": TYPE.get(s[3:], "other"), "trust": round(tr, 2), "group": None,
                                     "source": urls.get(rid) or "", "description": desc}})
    feats.sort(key=lambda f: f["properties"]["time"], reverse=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "events.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    (OUT / "groups.json").write_text("[]\n")
    print(f"{len(feats)} events, outcomes:", collections.Counter(
        "confirmed" if f["properties"]["trust"] == 0.9 else "contradicted" if f["properties"]["trust"] == 0.15
        else "other" for f in feats))

    # predictions: geometry model trained on all labelled rows, applied at the latest date
    cells = collections.defaultdict(lambda: [0.0] * len(HORIZONS))
    weekly = np.arange(0, D, 7)
    for i, h in enumerate(HORIZONS):
        t_tr, _, X_tr = P.rows(weekly[weekly + h < D], h)
        m = fit(X_tr, P.label(t_tr, _, h), GEO)
        t, j, X = P.rows(np.array([T]), h)
        p = m.predict_proba(mat(X, GEO))[:, 1]
        for jj, pp in zip(j, p):
            if pp < 0.02:
                continue
            g = int(P.gids[jj])
            c = h3.latlng_to_cell(st[g][1], st[g][2], 6)
            cells[c][i] = max(cells[c][i], round(float(pp), 3))
        print(f"h={h}: {int((p >= 0.5).sum())} settlements >= 0.5, {int((p >= 0.1).sum())} >= 0.1")
    (OUT / "data" / "h3").mkdir(parents=True, exist_ok=True)
    hz = [str(date.fromisoformat(as_of) + timedelta(days=h)) for h in HORIZONS]
    (OUT / "data" / "h3" / "predictions.json").write_text(json.dumps(
        {"model": "seraphim geo-gbm v0 (DeepState geometry, walk-forward AUC 0.95-0.98)",
         "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "asOf": as_of, "resolution": 6,
         "horizons": hz, "cells": cells}))
    lead = sorted(((c, x, n, s) for s, (c, x, n) in src_rec.items() if c + x >= 5),
                  key=lambda r: -(r[0] + 1) / (r[0] + r[1] + 2))
    kinds = dict(rec.execute("SELECT kind, count(*) FROM records GROUP BY 1"))
    stats = {"records": sum(kinds.values()), "by_kind": kinds,
             "sources": rec.execute("SELECT count(DISTINCT source) FROM records").fetchone()[0],
             "claims_v5": len(rows), "claims_total": len(rows),
             "claims_checked": sum(c + x for c, x, _ in src_rec.values()),
             "events_30d": len(feats), "events_since": cutoff, "settlements": len(P.gids), "truth_days": D, "as_of": as_of,
             "source_trust": [{"source": s, "confirmed": c, "contradicted": x, "claims": n,
                               "trust": round((c + 1) / (c + x + 2), 2)} for c, x, n, s in lead]}
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps({k: v for k, v in stats.items() if k != "source_trust"}))
    for r in stats["source_trust"][:8] + stats["source_trust"][-5:]:
        print("  ", r)


if __name__ == "__main__":
    main()
