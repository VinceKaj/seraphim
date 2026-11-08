"""Validation numbers for the stats page (app/public/validation.json).

Sections: truth sources (DeepState vs ISW), forecast backtest (walk-forward), Polymarket head-to-head by horizon
and calibration, claims vs DeepState by source side and claim type, extraction tuning (pilot/silver).
Usage: uv run trust/export_validation.py
"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np


def load(path):
    """np.load on .npz re-reads (and decompresses) an array on every access; load each array once."""
    z = np.load(path)
    return {k: z[k] for k in z.files}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import DATA, TRUST_DB  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "public" / "validation.json"


def truth_agreement():
    T = load(DATA / "truth.npz")
    I = load(DATA / "isw_truth.npz")
    dcol = {str(d)[:10]: i for i, d in enumerate(T["dates"])}
    gcol = {int(g): j for j, g in enumerate(T["gids"])}
    ij = np.array([gcol.get(int(g), -1) for g in I["gids"]])
    keep = ij >= 0
    cm = np.zeros((3, 3), np.int64)
    days = 0
    for k, d in enumerate(I["dates"]):
        d = str(d)[:10]
        if not I["have"][k] or d not in dcol:
            continue
        t = dcol[d]
        near = np.abs(T["dist"][t, ij[keep]]) < 20
        ds = T["state"][t, ij[keep]][near] + 1
        isw = I["state"][k, keep][near] + 1
        np.add.at(cm, (ds, isw), 1)
        days += 1
    tot = cm.sum()
    ru_agree = (cm[2, 2] + cm[:2, :2].sum()) / tot
    return dict(days=days, settlement_days=int(tot), exact_agree=round(float(np.trace(cm) / tot), 3),
                ru_vs_not_agree=round(float(ru_agree), 3), labels=["ua", "contested", "ru"],
                matrix=cm.tolist(),
                ds_ru_isw_not=round(float(cm[2, :2].sum() / max(cm[2].sum(), 1)), 3),
                isw_ru_ds_not=round(float(cm[:2, 2].sum() / max(cm[:, 2].sum(), 1)), 3))


def backtest():
    b = json.load(open(DATA / "backtest.json"))
    return {h: {k: {m: v[m] for m in ("base", "geo", "geo+text")} for k, v in r.items()} for h, r in b.items()}


def pm(path):
    rows = json.load(open(path))
    buckets = [("≤7 d", 0, 7), ("8–30 d", 8, 30), ("31–90 d", 31, 90), (">90 d", 91, 10_000)]
    out = []
    for name, a, b in buckets:
        rs = [r for r in rows if a <= r["h"] <= b]
        if not rs:
            continue
        o = np.array([r["ours"] for r in rs]); m = np.array([r["market"] for r in rs]); y = np.array([r["y"] for r in rs])
        out.append(dict(bucket=name, n=len(rs), markets=len({r["cid"] for r in rs}),
                        brier_ours=round(float(((o - y) ** 2).mean()), 4), brier_market=round(float(((m - y) ** 2).mean()), 4),
                        days_ours_closer=round(float((np.abs(o - y) < np.abs(m - y)).mean()), 3)))
    by = collections.defaultdict(list)
    for r in rows:
        by[r["cid"]].append(r)
    better = sum(np.mean([(r["ours"] - r["y"]) ** 2 for r in rs]) < np.mean([(r["market"] - r["y"]) ** 2 for r in rs])
                 for rs in by.values())
    by_q = {}
    for q in ("any", "capture", "all"):
        rs = [r for r in rows if r["q"] == q]
        if rs:
            o = np.array([r["ours"] for r in rs]); m = np.array([r["market"] for r in rs]); y = np.array([r["y"] for r in rs])
            by_q[q] = dict(n=len(rs), markets=len({r["cid"] for r in rs}), brier_ours=round(float(((o - y) ** 2).mean()), 4),
                           brier_market=round(float(((m - y) ** 2).mean()), 4))
    cal = []
    edges = [0, 0.05, 0.2, 0.5, 0.8, 1.0001]
    for lo, hi in zip(edges[:-1], edges[1:]):
        row = dict(bin=f"{lo:.0%}–{min(hi, 1):.0%}")
        for k in ("ours", "market"):
            sel = [r["y"] for r in rows if lo <= r[k] < hi]
            row[k] = dict(n=len(sel), rate=round(float(np.mean(sel)), 3) if sel else None)
        cal.append(row)
    y = np.array([r["y"] for r in rows]); o = np.array([r["ours"] for r in rows]); m = np.array([r["market"] for r in rows])
    return dict(markets=len(by), market_days=len(rows), better=int(better), buckets=out, by_qualifier=by_q,
                calibration=cal, brier_ours=round(float(((o - y) ** 2).mean()), 4),
                brier_market=round(float(((m - y) ** 2).mean()), 4))


def claims():
    db = sqlite3.connect(TRUST_DB)
    T = load(DATA / "truth.npz")
    col = {int(g): j for j, g in enumerate(T["gids"])}
    last = T["state"][-1]
    D = len(T["dates"])
    d0 = np.datetime64(str(T["dates"][0])[:10])
    side = dict(db.execute("SELECT author, side FROM source_side"))
    gain_ru, gain_ua = {"ru_captured", "ru_entered", "ua_withdrew"}, {"ua_captured", "ua_entered", "ru_withdrew"}
    agg = collections.defaultdict(lambda: [0, 0, 0])
    for src, g, s, pa in db.execute("SELECT source, gid, state, posted_at FROM claim_llm WHERE version='v5' AND gid IS NOT NULL"):
        if s not in gain_ru | gain_ua or g not in col:
            continue
        t = int((np.datetime64(pa[:10]) - d0).astype(int))
        jj = col[g]
        agree = (lambda a: a >= 0) if s in gain_ru else (lambda a: a <= 0)
        if t < 1 or t >= D or agree(T["state"][t - 1, jj]):
            continue                                   # echo of what DeepState already showed
        ok = bool(agree(T["state"][t:min(t + 90, D - 1) + 1, jj]).any())
        res = 0 if ok else (1 if D - 1 - t >= 90 else 2)
        for key in (("side", side.get(src) or "unknown"), ("claim", s)):
            agg[key][res] += 1
    fmt = lambda k: [dict(name=n, confirmed=v[0], contradicted=v[1], pending=v[2],  # noqa: E731
                          rate=round(v[0] / max(v[0] + v[1], 1), 3)) for (kk, n), v in sorted(agg.items()) if kk == k]
    rx = db.execute("SELECT count(*), sum(outcome='confirmed'), sum(outcome='contradicted'), sum(outcome='echo') "
                    "FROM claim_outcome").fetchone()
    lead = [r[0] for r in db.execute("SELECT lead_days FROM claim_outcome WHERE outcome='confirmed' AND lead_days IS NOT NULL")]
    return dict(by_side=fmt("side"), by_claim=fmt("claim"),
                mod_regex=dict(claims=rx[0], confirmed=rx[1], contradicted=rx[2], echo=rx[3],
                               lead_days_median=float(np.median(lead)) if lead else None))


EXTRACTION = [
    dict(version="v1", claims=50, resolved=0.96, silver=None, fix="actor ambiguous on repelled attacks → side in state label"),
    dict(version="v2", claims=44, resolved=0.932, silver=None, fix="'наши' resolved to wrong side → channel side in prompt"),
    dict(version="v3", claims=203, resolved=0.97, silver=0.991, fix="long lists silently truncated → max_tokens + stop_reason check"),
    dict(version="v4", claims=211, resolved=0.844, silver=0.956, fix="Kursk villages matched UA namesakes → Ukrainian name + RU-oblast drop"),
    dict(version="v5", claims=206, resolved=0.976, silver=0.965, fix="MoD strike lists counted as claims → excluded"),
]


def example(question="Will Russia capture Kostyantynivka by March 31?"):
    """One resolved market, day by day: Polymarket close vs our walk-forward P, plus DeepState distance to the line."""
    import csv
    q = next(r for r in csv.DictReader(open(Path(__file__).resolve().parent / "research" / "pm_territorial.csv"))
             if r["question"] == question)
    rs = sorted((r for r in json.load(open(DATA / "pm_compare_geo.json")) if r["cid"] == q["condition_id"]),
                key=lambda r: r["day"])
    T = load(DATA / "truth.npz")
    db = sqlite3.connect(TRUST_DB)
    g = db.execute("SELECT gid FROM settlement WHERE name = ? ORDER BY pop DESC LIMIT 1", (rs[0]["name"],)).fetchone()[0]
    j = {int(x): k for k, x in enumerate(T["gids"])}[g]
    di = {str(d)[:10]: k for k, d in enumerate(T["dates"])}
    series = [dict(day=r["day"], market=r["market"], ours=r["ours"],
                   dist=round(float(T["dist"][di[r["day"]], j]), 1) if r["day"] in di else None) for r in rs]
    o = np.array([r["ours"] for r in rs]); m = np.array([r["market"] for r in rs]); y = rs[0]["y"]
    return dict(question=question, deadline=q["deadline"], resolved=q["resolved"], volume=round(float(q["volume"])),
                rule=q["rule"], source=q["resolution_source"], series=series,
                brier_ours=round(float(((o - y) ** 2).mean()), 4), brier_market=round(float(((m - y) ** 2).mean()), 4),
                dist_now=round(float(T["dist"][-1, j]), 1), as_of=str(T["dates"][-1])[:10])


def market_curves(path=DATA / "pm_compare_geo.json"):
    """Per-market daily series (for the picker) + mean P by days-to-deadline, split by resolution."""
    import csv
    rows = json.load(open(path))
    meta = {r["condition_id"]: r for r in csv.DictReader(open(Path(__file__).resolve().parent / "research" / "pm_territorial.csv"))}
    by = collections.defaultdict(list)
    for r in rows:
        by[r["cid"]].append(r)
    markets = []
    for cid, rs in by.items():
        rs.sort(key=lambda r: r["day"])
        m = meta.get(cid, {})
        y = rs[0]["y"]
        bo = float(np.mean([(r["ours"] - y) ** 2 for r in rs])); bm = float(np.mean([(r["market"] - y) ** 2 for r in rs]))
        markets.append(dict(question=m.get("question", rs[0]["name"]), deadline=m.get("deadline", ""), q=rs[0]["q"],
                            resolved="Yes" if y else "No", volume=round(float(m.get("volume") or 0)),
                            brier_ours=round(bo, 4), brier_market=round(bm, 4),
                            series=[dict(day=r["day"], market=round(r["market"], 3), ours=round(r["ours"], 4)) for r in rs]))
    markets.sort(key=lambda m: -m["volume"])
    bins = [(0, 7), (8, 14), (15, 30), (31, 60), (61, 90), (91, 120), (121, 180)]
    curves = {}
    for yv, name in ((1, "yes"), (0, "no")):
        out = []
        for a, b in bins:
            rs = [r for r in rows if r["y"] == yv and a <= r["h"] <= b]
            if rs:
                out.append(dict(h0=a, h1=b, n=len(rs), markets=len({r["cid"] for r in rs}),
                                ours=round(float(np.mean([r["ours"] for r in rs])), 3),
                                market=round(float(np.mean([r["market"] for r in rs])), 3)))
        curves[name] = out
    return dict(markets=markets, curves=curves)


def main():
    v = dict(example=example(), market_curves=market_curves(), truth=truth_agreement(), backtest=backtest(), pm_deepstate=pm(DATA / "pm_compare_geo.json"),
             pm_isw=pm(DATA / "pm_compare_geo_isw.json"), claims=claims(), extraction=EXTRACTION)
    OUT.write_text(json.dumps(v, ensure_ascii=False, indent=1))
    print(json.dumps({k: v[k] for k in ("truth",)}, indent=1))
    print(json.dumps(v["pm_deepstate"]["buckets"], indent=0))
    print(json.dumps(v["claims"]["by_side"]), v["claims"]["mod_regex"])


if __name__ == "__main__":
    main()
