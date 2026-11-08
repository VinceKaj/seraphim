"""Compare extraction prompt versions on the same posts (the tuning harness).

Metrics per version:
  pilot  (stratified last-week sample): posts ok / truncated / errors, claims, % resolved to a settlement,
         claims per post by source group, mean attempts.
  silver (100 mod_russia posts with regex labels): recall and agreement of ru_captured vs regex settlements.
  gold   (trust/gold/pilot_review.md marked by a human, when present): precision of reviewed claims.
Usage: uv run trust/eval_extract.py [v3 v4 ...]
"""
import collections
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
db = sqlite3.connect(ROOT / "trust.db")
SILVER = [l.strip() for l in open(ROOT / "gold" / "silver_mod_ids.txt") if l.strip()]
versions = sys.argv[1:] or [v for v, in db.execute("SELECT DISTINCT version FROM extract_run ORDER BY 1")]


def silver(v):
    q = ",".join("?" * len(SILVER))
    rx, ll = collections.defaultdict(set), collections.defaultdict(set)
    for r, g in db.execute(f"SELECT record_id, gid FROM claim_regex WHERE in_scope=1 AND record_id IN ({q})", SILVER):
        rx[r].add(g)
    n = db.execute(f"SELECT count(*) FROM extract_run WHERE version=? AND record_id IN ({q})", (v, *SILVER)).fetchone()[0]
    if not n:
        return None
    for r, g in db.execute(f"SELECT record_id, gid FROM claim_llm WHERE version=? AND state='ru_captured' AND gid IS NOT NULL "
                           f"AND record_id IN ({q})", (v, *SILVER)):
        ll[r].add(g)
    tp = sum(len(rx[r] & ll[r]) for r in SILVER)
    fn = sum(len(rx[r] - ll[r]) for r in SILVER)
    extra = sum(len(ll[r] - rx[r]) for r in SILVER)
    return dict(recall=round(tp / max(tp + fn, 1), 3), tp=tp, fn=fn, extra_vs_regex=extra)


def pilot(v):
    r = db.execute("""SELECT count(*), sum(status='ok'), sum(status LIKE '%truncated%'), sum(status LIKE 'llm_error%'
        OR status LIKE 'exception%'), round(avg(attempts),2) FROM extract_run WHERE version=? AND sample='pilot'""",
                   (v,)).fetchone()
    c = db.execute("""SELECT count(*), sum(gid IS NOT NULL) FROM claim_llm c JOIN extract_run e USING(record_id, version)
        WHERE c.version=? AND e.sample='pilot'""", (v,)).fetchone()
    return dict(posts=r[0], ok=r[1], truncated=r[2], errors=r[3], attempts=r[4], claims=c[0],
                resolved=round((c[1] or 0) / max(c[0], 1), 3))


def gold(v):
    p = ROOT / "gold" / f"pilot_review_{v}.md"
    if not p.exists():
        return None
    t = p.read_text()
    ok, bad = len(re.findall(r"^- \[x\]", t, re.M)), len(re.findall(r"^- \[ \]", t, re.M))
    return dict(reviewed_claims=ok + bad, precision=round(ok / max(ok + bad, 1), 3),
                missed_notes=len(re.findall(r"^MISSED: \S", t, re.M)))


for v in versions:
    print(v, "pilot", pilot(v), "| silver", silver(v), "| gold", gold(v))
