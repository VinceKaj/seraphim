"""Acceptance tests against the running record-store service."""
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
import requests

import store_client as sc

from store import DB_PATH as DB
NOW = datetime.now(timezone.utc)


def cleanup():
    c = sqlite3.connect(DB, timeout=30)
    with c:
        ids = "SELECT id FROM records WHERE source LIKE 'test:%'"
        c.execute(f"DELETE FROM links WHERE from_id IN ({ids})")
        c.execute("DELETE FROM records WHERE source LIKE 'test:%'")
        c.execute("DELETE FROM sources WHERE author LIKE 'test:%'")
    c.close()


@pytest.fixture(scope="session", autouse=True)
def clean_db():
    cleanup()
    yield
    cleanup()


def post(text="hello", links=(), **kw):
    d = {"author": "test:chan", "posted_at": NOW.isoformat(), "scraped_at": NOW.isoformat(),
         "text": text, "links": list(links), "raw": {"id": 1, "message": text}}
    d.update(kw)
    return d


def raw_post(kind, source, source_id, data):
    return requests.post(sc.STORE_URL + "/records",
                         json={"kind": kind, "source": source, "source_id": source_id, "data": data})


# 1
def test_health_and_bind():
    h = sc.health()
    assert h["ok"] is True and "count" in h and "max_seq" in h and "by_kind" in h
    out = subprocess.run(["ss", "-tln"], capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines() if ":8100 " in l]
    assert lines and all("127.0.0.1:8100" in l for l in lines)
    assert not any("0.0.0.0:8100" in l or "[::]:8100" in l or "*:8100" in l for l in lines)


# 2
def test_idempotent_insert():
    a = sc.create("post", "test:idem", "1", post())
    b = sc.create("post", "test:idem", "1", post())
    assert a["new"] is True and b["new"] is False
    assert a["id"] == b["id"] == sc.rid("post|test:idem|1") and a["seq"] == b["seq"]
    assert sc.read(a["id"])["data"]["text"] == "hello"


# 3
@pytest.mark.parametrize("kind,data", [
    ("post", {k: v for k, v in post().items() if k != "raw"}),
    ("post", post(posted_at="2026-11-07T10:00:00")),
    ("nonsense", post()),
])
def test_invalid_rejected(kind, data):
    r = raw_post(kind, "test:bad", "1", data)
    assert r.status_code == 422, r.text


def test_utc_normalised():
    r = sc.create("post", "test:tz", "1", post(posted_at="2026-11-07T12:00:00+03:00"))
    assert sc.read(r["id"])["data"]["posted_at"] == "2026-11-07T09:00:00Z"


# 4
def test_claim_empty_evidence():
    r = raw_post("claim", "test:extractor", "x:0", {"text": "c", "evidence": []})
    assert r.status_code == 422, r.text


# 5
def test_forward_unresolved_then_resolved():
    a_key = sc.key("post", "test:fwd_orig", "100")
    a_id = sc.rid(a_key)
    b = sc.create("post", "test:fwd", "1", post(links=[{"type": "forward", "target": a_key}]))
    un = {u["target"]: u for u in sc.unresolved(type="forward", limit=1000)}
    assert un[a_key]["count"] == 1 and un[a_key]["target_id"] == a_id
    assert sc.links(b["id"])[0]["resolved"] is False
    sc.create("post", "test:fwd_orig", "100", post("original"))
    assert sc.backlinks(a_id, type="forward") == [{"from_id": b["id"], "type": "forward"}]
    assert a_key not in {u["target"] for u in sc.unresolved(type="forward", limit=1000)}
    assert sc.links(b["id"])[0]["resolved"] is True


# 6
def test_album_grouping():
    album = "post|telegram:test|album:42"
    ids = {sc.create("post", "test:album", str(i), post(links=[{"type": "album", "target": album}]))["id"]
           for i in range(3)}
    bl = sc.backlinks(sc.rid(album), type="album")
    assert {b["from_id"] for b in bl} == ids and len(bl) == 3


# 7
def test_edit_of():
    orig = sc.create("post", "test:edit", "5", post("v1"))
    ed = sc.create("post", "test:edit", "5@1762500000",
                   post("v2", is_edit=True, links=[{"type": "edit_of", "target": "post|test:edit|5"}]))
    assert ed["id"] != orig["id"]
    (l,) = sc.links(ed["id"])
    assert l["type"] == "edit_of" and l["target_id"] == orig["id"] and l["resolved"] is True
    assert sc.backlinks(orig["id"], type="edit_of") == [{"from_id": ed["id"], "type": "edit_of"}]


# 8
def test_claim_evidence_backlink():
    a = sc.create("post", "test:claimsrc", "1", post("Russian forces entered Kupiansk"))
    c = sc.create("claim", "test:extractor-0", f"{a['id']}:0",
                  {"text": "RU forces in Kupiansk", "confidence": 0.4,
                   "evidence": [{"record": a["id"], "span": "entered Kupiansk"}]})
    assert sc.backlinks(a["id"], type="evidence") == [{"from_id": c["id"], "type": "evidence"}]
    assert sc.read(c["id"])["data"]["confidence"] == 0.4  # extra fields kept


# 9
def test_tail():
    x = sc.health()["max_seq"]
    p1 = sc.create("post", "test:tail", "1", post())
    sc.create("market", "test:tail", "m@1", {"market_id": "m", "question": "q?", "prices": {"Yes": 0.2},
                                            "snapshot_at": NOW.isoformat(), "raw": {}})
    p2 = sc.create("post", "test:tail", "2", post())
    got = sc.tail(since=x, kind="post")
    assert [r["seq"] for r in got] == [p1["seq"], p2["seq"]]
    assert all(r["kind"] == "post" and r["seq"] > x for r in got)
    assert len(sc.tail(since=x, source="test:tail")) == 3


# 10
def test_sources_roundtrip():
    sc.put_source("test:rybar", {"side": "ru", "reliability": 0.6})
    assert sc.get_source("test:rybar")["data"] == {"side": "ru", "reliability": 0.6}
    sc.put_source("test:rybar", {"side": "ru", "reliability": 0.3, "notes": "milblogger"})
    assert sc.get_source("test:rybar")["data"] == {"side": "ru", "reliability": 0.3, "notes": "milblogger"}
    assert "test:rybar" in {s["author"] for s in requests.get(sc.STORE_URL + "/sources").json()}


# 11
def test_concurrency():
    def worker(t):
        s = requests.Session()
        out = []
        for i in range(25):
            r = s.post(sc.STORE_URL + "/records", json={"kind": "post", "source": "test:conc",
                                                        "source_id": f"{t}-{i}", "data": post(f"{t}-{i}")})
            r.raise_for_status()
            out.append(r.json()["new"])
        return out

    with ThreadPoolExecutor(20) as ex:
        results = [n for chunk in ex.map(worker, range(20)) for n in chunk]
    assert len(results) == 500 and all(results)
    assert len(sc.tail(source="test:conc", limit=1000)) == 500
