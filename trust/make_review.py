"""Write a human review file for one extraction version: trust/gold/pilot_review_<version>.md.
Mark each claim line [x] if correct (settlement, state and span all right); leave [ ] if wrong.
Write missed claims after "MISSED:" for each post. eval_extract.py reads the file back.
Usage: uv run trust/make_review.py v5 [--n 50]
"""
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
v = sys.argv[1]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 50
db = sqlite3.connect(ROOT / "trust.db")
rec = sqlite3.connect(f"file:{ROOT.parent / 'record-store' / 'records.db'}?mode=ro", uri=True)
runs = db.execute("SELECT record_id, source, posted_at, n_claims FROM extract_run WHERE version=? AND sample='pilot'",
                  (v,)).fetchall()
rnd = random.Random(1)
with_c = [r for r in runs if r[3]]
without = [r for r in runs if not r[3]]
pick = rnd.sample(with_c, min(len(with_c), int(n * 0.8))) + rnd.sample(without, min(len(without), n - int(n * 0.8)))
nm = dict(db.execute("SELECT gid, name FROM settlement"))
out = [f"# Extraction review — prompt {v}\n",
       "Mark `[x]` when the claim is right (settlement, state, side, span). Leave `[ ]` when wrong; add a note after `—`.",
       "After `MISSED:` list claims the model did not find (e.g. `ru_captured Світле`). Leave empty if none.\n"]
for rid, src, pa, k in sorted(pick, key=lambda r: r[1]):
    url, text = rec.execute("SELECT json_extract(data,'$.url'), json_extract(data,'$.text') FROM records WHERE id=?",
                            (rid,)).fetchone()
    t = (text or "").strip()
    out.append(f"\n---\n## {src} · {pa[:16]} · {url or rid}\n")
    out.append("> " + (t[:1800] + (" …[cut]" if len(t) > 1800 else "")).replace("\n", "\n> ") + "\n")
    for name, gid, state, mod, span in db.execute("SELECT name, gid, state, modality, span FROM claim_llm WHERE "
                                                    "record_id=? AND version=? ORDER BY n", (rid, v)):
        out.append(f"- [ ] **{state}** {name} → {nm.get(gid, 'UNRESOLVED')} ({mod}) · “{span[:160]}”")
    out.append("\nMISSED: \n")
p = ROOT / "gold" / f"pilot_review_{v}.md"
p.write_text("\n".join(out))
print(p, len(pick), "posts")
