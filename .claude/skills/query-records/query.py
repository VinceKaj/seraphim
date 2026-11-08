#!/usr/bin/env python3
"""Read-only SQL over the Seraphim record store (SQLite).

  query.py "SELECT ..."                 # table output, max 200 rows
  query.py -f json "SELECT ..."         # json | jsonl | csv | table
  query.py -n 1000 -t 30 "SELECT ..."   # row cap, timeout in seconds
  query.py --schema                     # tables, indexes, row counts
  echo "SELECT ..." | query.py -        # SQL from stdin

The DB is opened with mode=ro and PRAGMA query_only=ON: writes are refused by SQLite.
DB path: --db, else $STORE_DB, else <repo>/record-store/records.db.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[3] / "record-store" / "records.db"


def connect(path: str) -> sqlite3.Connection:
    if not Path(path).exists():
        sys.exit(f"error: database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    # ATTACH/DETACH could create files elsewhere; deny them.
    deny = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in deny else sqlite3.SQLITE_OK)
    return conn


def schema(conn: sqlite3.Connection) -> None:
    for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') "
            "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"):
        print(sql + ";")
    print()
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        print(f"-- {t}: {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
    print("-- records by kind:", dict(conn.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind")))


def cell(v, width: int | None):
    if v is None:
        return "NULL"
    s = str(v).replace("\n", "\\n")
    return s if width is None or len(s) <= width else s[: width - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sql", nargs="?", help="SQL statement, or '-' to read from stdin")
    ap.add_argument("-f", "--format", choices=["table", "json", "jsonl", "csv"], default="table")
    ap.add_argument("-n", "--limit", type=int, default=200, help="max rows to print (default 200, 0 = no cap)")
    ap.add_argument("-t", "--timeout", type=float, default=20.0, help="abort query after N seconds (default 20)")
    ap.add_argument("-w", "--width", type=int, default=80, help="max cell width in table format (0 = no cap)")
    ap.add_argument("--db", default=os.environ.get("STORE_DB", str(DEFAULT_DB)))
    ap.add_argument("--schema", action="store_true", help="print schema and row counts")
    a = ap.parse_args()

    conn = connect(a.db)
    if a.schema:
        return schema(conn)
    if not a.sql:
        ap.error("give SQL or --schema")
    sql = sys.stdin.read() if a.sql == "-" else a.sql

    deadline = time.monotonic() + a.timeout
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(a.limit + 1) if a.limit else cur.fetchall()
    except sqlite3.OperationalError as e:
        msg = "query timed out" if "interrupted" in str(e) else str(e)
        sys.exit(f"error: {msg}")
    except sqlite3.DatabaseError as e:
        sys.exit(f"error: {e}")
    truncated = bool(a.limit) and len(rows) > a.limit
    rows = rows[: a.limit] if a.limit else rows

    if a.format == "json":
        json.dump([dict(zip(cols, r)) for r in rows], sys.stdout, ensure_ascii=False, indent=1, default=str)
        print()
    elif a.format == "jsonl":
        for r in rows:
            print(json.dumps(dict(zip(cols, r)), ensure_ascii=False, default=str))
    elif a.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(cols)
        w.writerows(rows)
    else:
        width = a.width or None
        cells = [[cell(v, width) for v in r] for r in rows]
        widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]
        print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
        print("-+-".join("-" * w for w in widths))
        for r in cells:
            print(" | ".join(v.ljust(widths[i]) for i, v in enumerate(r)))
    print(f"-- {len(rows)} row(s)" + (f", truncated at {a.limit} (use -n)" if truncated else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
