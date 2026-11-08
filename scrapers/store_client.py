"""Thin client for the Seraphim record store. Base URL from $STORE_URL."""
import hashlib
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import requests

STORE_URL = os.environ.get("STORE_URL", "http://127.0.0.1:8100").rstrip("/")
_s = requests.Session()


def key(kind: str, source: str, source_id: str) -> str:
    return f"{kind}|{source}|{source_id}"


def rid(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    raise TypeError(f"not JSON serialisable: {type(o)}")


def _req(method: str, path: str, **kw) -> Any:
    r = _s.request(method, STORE_URL + path, timeout=60, **kw)
    if r.status_code >= 400:
        raise requests.HTTPError(f"{r.status_code} {r.text}", response=r)
    return r.json()


def create(kind: str, source: str, source_id: str, data) -> dict:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    body = json.dumps({"kind": kind, "source": source, "source_id": str(source_id), "data": data},
                      default=_default)
    return _req("POST", "/records", data=body, headers={"Content-Type": "application/json"})


def read(id: str) -> dict:
    return _req("GET", f"/records/{id}")


def tail(since: int = 0, kind: str | None = None, source: str | None = None, limit: int = 100) -> list[dict]:
    params = {"since": since, "limit": limit, "kind": kind, "source": source}
    return _req("GET", "/records", params={k: v for k, v in params.items() if v is not None})


def links(id: str) -> list[dict]:
    return _req("GET", f"/records/{id}/links")


def backlinks(id: str, type: str | None = None) -> list[dict]:
    return _req("GET", f"/records/{id}/backlinks", params={"type": type} if type else None)


def unresolved(type: str | None = None, limit: int = 100) -> list[dict]:
    params = {"limit": limit, **({"type": type} if type else {})}
    return _req("GET", "/unresolved", params=params)


def put_source(author: str, data: dict) -> dict:
    return _req("PUT", f"/sources/{author}", data=json.dumps(data, default=_default),
                headers={"Content-Type": "application/json"})


def get_source(author: str) -> dict:
    return _req("GET", f"/sources/{author}")


def health() -> dict:
    return _req("GET", "/health")


def follow(kind: str | None = None, source: str | None = None,
           cursor_file: str = ".store_cursor", poll: float = 3.0) -> Iterator[dict]:
    """Tail forever. Persists last seq to cursor_file after each yielded record."""
    path = Path(cursor_file)
    since = int(path.read_text().strip() or 0) if path.exists() else 0
    while True:
        batch = tail(since=since, kind=kind, source=source, limit=1000)
        if not batch:
            time.sleep(poll)
            continue
        for rec in batch:
            yield rec
            since = rec["seq"]
            path.write_text(str(since))
