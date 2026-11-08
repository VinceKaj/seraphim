"""Score claims against DeepState truth.

For a capture claim on settlement g posted at day t:
  echo          DeepState already shows g as RU at t           lead_days = (start of that RU run) - t  (<= 0)
  confirmed     DeepState first shows g as RU at t' in (t, t+H]  lead_days = t' - t  (> 0)
  contradicted  not RU at any day in (t, t+H]
  pending       t + H is after the last DeepState day
Contested days (state 0) do not count as RU. A claim repeated by the same channel for the same settlement
within DEDUP days counts once (earliest).
Output: trust.db claim_outcome(record_id, n, channel, gid, t, state_t, outcome, lead_days, H)
Usage: uv run trust/outcomes.py [--H 30]
"""
import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import DATA, TRUST_DB  # noqa: E402

DEDUP = 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--H", type=int, default=30)
    ap.add_argument("--table", default="claim_regex")
    a = ap.parse_args()
    T = np.load(DATA / "truth.npz")
    dates, gids, state = T["dates"], T["gids"], T["state"]
    col = {int(g): j for j, g in enumerate(gids)}
    d0, last = dates[0].astype(object), len(dates) - 1
    db = sqlite3.connect(TRUST_DB)
    rows = db.execute(f"SELECT record_id, n, channel, gid, posted_at FROM {a.table} WHERE in_scope=1 "
                      "ORDER BY channel, gid, posted_at").fetchall()
    out, prev = [], {}
    for rid, n, ch, g, pa in rows:
        t = (date.fromisoformat(pa[:10]) - d0).days
        if t < 0 or g not in col:
            continue
        if (ch, g) in prev and t - prev[(ch, g)] <= DEDUP:
            continue
        prev[(ch, g)] = t
        s = state[:, col[g]]
        st = int(s[t])
        if st == 1:
            k = t
            while k > 0 and s[k - 1] == 1:
                k -= 1
            oc, lead = "echo", k - t
        else:
            nxt = np.flatnonzero(s[t + 1: t + 1 + a.H] == 1)
            if nxt.size:
                oc, lead = "confirmed", int(nxt[0]) + 1
            elif t + a.H > last:
                oc, lead = "pending", None
            else:
                oc, lead = "contradicted", None
        out.append((rid, n, ch, g, pa[:10], st, oc, lead, a.H))
    db.executescript("DROP TABLE IF EXISTS claim_outcome; CREATE TABLE claim_outcome(record_id TEXT, n INTEGER, "
                     "channel TEXT, gid INTEGER, t TEXT, state_t INTEGER, outcome TEXT, lead_days INTEGER, H INTEGER);")
    db.executemany("INSERT INTO claim_outcome VALUES (?,?,?,?,?,?,?,?,?)", out)
    db.commit()
    print(f"H={a.H} days, {len(out)} deduplicated claims")
    for r in db.execute("""SELECT channel, count(*) n, sum(outcome='echo') echo, sum(outcome='confirmed') conf,
        sum(outcome='contradicted') contra, sum(outcome='pending') pend,
        round(100.0*sum(outcome='confirmed')/nullif(sum(outcome in ('confirmed','contradicted')),0),1) conf_pct
        FROM claim_outcome GROUP BY 1 ORDER BY 2 DESC"""):
        print(r)


if __name__ == "__main__":
    main()
