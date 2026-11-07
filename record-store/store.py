"""Local immutable record store. Run: uv run uvicorn store:app --host 127.0.0.1 --port 8100"""
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from schemas import SCHEMAS

DB_PATH = os.environ.get("STORE_DB", str(Path(__file__).resolve().parent / "records.db"))
KEY_RE = re.compile(r"^(post|article|market|claim)\|")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE NOT NULL,
  key TEXT NOT NULL,
  kind TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_kind_seq ON records(kind, seq);
CREATE INDEX IF NOT EXISTS ix_source ON records(source);

CREATE TABLE IF NOT EXISTS links(
  from_id TEXT NOT NULL, type TEXT NOT NULL,
  target TEXT NOT NULL,
  target_id TEXT,
  PRIMARY KEY(from_id, type, target));
CREATE INDEX IF NOT EXISTS ix_links_target ON links(target_id);

CREATE TABLE IF NOT EXISTS sources(
  author TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at REAL NOT NULL);
"""


def rid(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_app):
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA_SQL)
    yield


app = FastAPI(title="Seraphim record store", lifespan=lifespan)


class RecordIn(BaseModel):
    kind: str
    source: str
    source_id: str
    data: dict[str, Any]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["data"] = json.loads(d["data"])
    return d


@app.post("/records")
def create_record(rec: RecordIn):
    model = SCHEMAS.get(rec.kind)
    if model is None:
        raise HTTPException(422, f"unknown kind {rec.kind!r}; expected one of {sorted(SCHEMAS)}")
    try:
        obj = model.model_validate(rec.data)
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))
    data = obj.model_dump(mode="json")
    key = f"{rec.kind}|{rec.source}|{rec.source_id}"
    id_ = rid(key)

    links = [(id_, l["type"], l["target"], rid(l["target"]) if KEY_RE.match(l["target"]) else None)
             for l in data.get("links", [])]
    if rec.kind == "claim":
        links += [(id_, "evidence", ev["record"], ev["record"]) for ev in data["evidence"]]

    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = c.execute(
                "INSERT OR IGNORE INTO records(id,key,kind,source,source_id,created_at,data) "
                "VALUES(?,?,?,?,?,?,?)",
                (id_, key, rec.kind, rec.source, rec.source_id, time.time(), json.dumps(data)))
            new = cur.rowcount == 1
            if new:
                seq = cur.lastrowid
                c.executemany("INSERT OR IGNORE INTO links(from_id,type,target,target_id) VALUES(?,?,?,?)", links)
            else:
                seq = c.execute("SELECT seq FROM records WHERE id=?", (id_,)).fetchone()[0]
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    return {"id": id_, "key": key, "seq": seq, "new": new}


@app.get("/records")
def tail(since: int = 0, kind: str | None = None, source: str | None = None, limit: int = 100):
    limit = max(1, min(limit, 1000))
    q, args = "SELECT * FROM records WHERE seq > ?", [since]
    if kind:
        q += " AND kind = ?"; args.append(kind)
    if source:
        q += " AND source = ?"; args.append(source)
    q += " ORDER BY seq ASC LIMIT ?"; args.append(limit)
    with db() as c:
        return [_row(r) for r in c.execute(q, args)]


@app.get("/records/{id}")
def read_record(id: str):
    with db() as c:
        r = c.execute("SELECT * FROM records WHERE id=?", (id,)).fetchone()
    if r is None:
        raise HTTPException(404, "record not found")
    return _row(r)


@app.get("/records/{id}/links")
def record_links(id: str):
    with db() as c:
        rows = c.execute(
            "SELECT l.type, l.target, l.target_id, "
            "EXISTS(SELECT 1 FROM records r WHERE r.id = l.target_id) AS resolved "
            "FROM links l WHERE l.from_id=? ORDER BY l.type, l.target", (id,)).fetchall()
    return [{**dict(r), "resolved": bool(r["resolved"])} for r in rows]


@app.get("/records/{id}/backlinks")
def record_backlinks(id: str, type: str | None = None):
    q, args = "SELECT from_id, type FROM links WHERE target_id=?", [id]
    if type:
        q += " AND type=?"; args.append(type)
    with db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY from_id", args)]


@app.get("/unresolved")
def unresolved(type: str | None = None, limit: int = 100):
    limit = max(1, min(limit, 1000))
    q = ("SELECT l.target, l.target_id, COUNT(*) AS count FROM links l "
         "WHERE l.target_id IS NOT NULL AND l.type != 'evidence' "
         "AND NOT EXISTS(SELECT 1 FROM records r WHERE r.id = l.target_id)")
    args: list = []
    if type:
        q += " AND l.type=?"; args.append(type)
    q += " GROUP BY l.target ORDER BY count DESC, l.target LIMIT ?"; args.append(limit)
    with db() as c:
        return [dict(r) for r in c.execute(q, args)]


@app.put("/sources/{author:path}")
def put_source(author: str, data: dict[str, Any] = Body(...)):
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO sources(author,data,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(author) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                  (author, json.dumps(data), now))
    return {"author": author, "data": data, "updated_at": now}


@app.get("/sources")
def list_sources():
    with db() as c:
        return [_row(r) for r in c.execute("SELECT * FROM sources ORDER BY author")]


@app.get("/sources/{author:path}")
def get_source(author: str):
    with db() as c:
        r = c.execute("SELECT * FROM sources WHERE author=?", (author,)).fetchone()
    if r is None:
        raise HTTPException(404, "source not found")
    return _row(r)


@app.get("/health")
def health():
    with db() as c:
        count, max_seq = c.execute("SELECT COUNT(*), COALESCE(MAX(seq),0) FROM records").fetchone()
        by_kind = dict(c.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
    return {"ok": True, "count": count, "max_seq": max_seq, "by_kind": by_kind}
