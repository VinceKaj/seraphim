#!/usr/bin/env python3
"""Event API + static host. Standard library only (Python 3.8+)."""
import json
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "events.json"
INDEX_FILE = BASE / "index.html"
API_KEY = os.environ.get("API_KEY")  # if set, POST/PUT/DELETE require X-API-Key
PORT = int(os.environ.get("PORT", "8000"))
CATEGORIES = {"military", "diplomatic", "humanitarian", "political", "energy", "other"}
FIELDS = {"date", "title", "summary", "lat", "lng", "category", "source_url"}
REQUIRED = ("date", "title", "lat", "lng")
LOCK = threading.Lock()


def read_events():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def write_events(events):
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA_FILE)  # atomic swap, so a crash can't corrupt the file


def validate(data, partial=False):
    """Return a list of error strings. Empty list means valid."""
    if not isinstance(data, dict):
        return ["body must be a JSON object"]
    errs = []
    unknown = set(data) - FIELDS
    if unknown:
        errs.append(f"unknown fields: {sorted(unknown)}")
    if not partial:
        errs += [f"missing required field: {f}" for f in REQUIRED if f not in data]
    if "date" in data and not (isinstance(data["date"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["date"])):
        errs.append("date must be YYYY-MM-DD")
    if "title" in data and not (isinstance(data["title"], str) and data["title"].strip()):
        errs.append("title must be a non-empty string")
    if "lat" in data and not (isinstance(data["lat"], (int, float)) and -90 <= data["lat"] <= 90):
        errs.append("lat must be a number between -90 and 90")
    if "lng" in data and not (isinstance(data["lng"], (int, float)) and -180 <= data["lng"] <= 180):
        errs.append("lng must be a number between -180 and 180")
    if "category" in data and not (isinstance(data["category"], str) and data["category"] in CATEGORIES):
        errs.append(f"category must be one of {sorted(CATEGORIES)}")
    for f in ("summary", "source_url"):
        if f in data and not isinstance(data[f], str):
            errs.append(f"{f} must be a string")
    if data.get("source_url") and not re.match(r"https?://", data["source_url"]):
        errs.append("source_url must start with http:// or https://")
    return errs


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"null")
        except json.JSONDecodeError:
            return None

    def authorized(self):
        return API_KEY is None or self.headers.get("X-API-Key") == API_KEY

    def parts(self):
        return [p for p in urlparse(self.path).path.split("/") if p]

    def is_collection(self):
        return self.parts() == ["api", "events"]

    def is_item(self):
        p = self.parts()
        return len(p) == 3 and p[:2] == ["api", "events"]

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        if self.parts() in ([], ["index.html"]):
            return self._send(200, INDEX_FILE.read_bytes(), "text/html")
        if self.is_collection():
            cat = parse_qs(urlparse(self.path).query).get("category", [None])[0]
            with LOCK:
                events = read_events()
            if cat:
                events = [e for e in events if e.get("category") == cat]
            return self.send_json(200, sorted(events, key=lambda e: (e["date"], e["id"])))
        if self.is_item():
            with LOCK:
                ev = next((e for e in read_events() if e["id"] == self.parts()[2]), None)
            return self.send_json(200, ev) if ev else self.send_json(404, {"error": "not found"})
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.is_collection():
            return self.send_json(404, {"error": "not found"})
        if not self.authorized():
            return self.send_json(401, {"error": "missing or invalid X-API-Key"})
        data = self.read_body()
        errs = validate(data)
        if errs:
            return self.send_json(400, {"errors": errs})
        event = {"id": uuid.uuid4().hex, "summary": "", "category": "other", "source_url": "", **data}
        with LOCK:
            events = read_events()
            events.append(event)
            write_events(events)
        self.send_json(201, event)

    def do_PUT(self):
        if not self.is_item():
            return self.send_json(404, {"error": "not found"})
        if not self.authorized():
            return self.send_json(401, {"error": "missing or invalid X-API-Key"})
        data = self.read_body()
        errs = validate(data, partial=True)
        if errs:
            return self.send_json(400, {"errors": errs})
        with LOCK:
            events = read_events()
            ev = next((e for e in events if e["id"] == self.parts()[2]), None)
            if ev:
                ev.update(data)
                write_events(events)
        self.send_json(200, ev) if ev else self.send_json(404, {"error": "not found"})

    def do_DELETE(self):
        if not self.is_item():
            return self.send_json(404, {"error": "not found"})
        if not self.authorized():
            return self.send_json(401, {"error": "missing or invalid X-API-Key"})
        with LOCK:
            events = read_events()
            kept = [e for e in events if e["id"] != self.parts()[2]]
            found = len(kept) != len(events)
            if found:
                write_events(kept)
        self.send_json(204, {}) if found else self.send_json(404, {"error": "not found"})


if __name__ == "__main__":
    mode = "writes protected by API_KEY" if API_KEY else "writes are OPEN (set API_KEY to lock them)"
    print(f"Serving on http://localhost:{PORT}  ({mode})")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()