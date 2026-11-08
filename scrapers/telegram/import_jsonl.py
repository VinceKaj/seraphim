"""Import a Telegram channel dump (messages.jsonl) into the record store.

Usage: uv run scrapers/telegram/import_jsonl.py <messages.jsonl> [--author-name NAME] [--lang uk]

Input line format (one message per line):
  id, date, channel, text, views, forwards, replies, reactions, media_type,
  media_path, forwarded_from, reply_to_id, grouped_id, post_author, edit_date, url

Mapping:
- One `post` per message, source_id = str(id). The dump has only the latest
  text, so no `edit_of` records are made; edit_date stays in `raw`.
- reply_to_id -> `reply` link to the same channel.
- grouped_id  -> `album` link (virtual key).
- forwarded_from is a numeric channel id, not a username. No `forward` link
  is made (convention needs a username); the id stays in `raw`.
- scraped_at = file mtime (the real scrape time is unknown).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "record-store"))
import store_client as sc  # noqa: E402

MEDIA = {"Photo": "photo", "Document": "doc"}


def to_data(m: dict, scraped_at: datetime, author_name: str | None, lang: str | None) -> dict:
    ch = m["channel"]
    links = []
    if m.get("reply_to_id"):
        links.append({"type": "reply", "target": sc.key("post", f"telegram:{ch}", str(m["reply_to_id"]))})
    if m.get("grouped_id"):
        links.append({"type": "album", "target": sc.key("post", f"telegram:{ch}", f"album:{m['grouped_id']}")})
    media = []
    if m.get("media_type") in MEDIA:
        media.append({"type": MEDIA[m["media_type"]], "ref": m.get("media_path") or m["url"]})
    return {
        "url": m["url"],
        "author": f"telegram:{ch}",
        "author_name": author_name,
        "posted_at": m["date"],
        "scraped_at": scraped_at.isoformat(),
        "lang": lang,
        "text": m.get("text") or "",
        "views": m.get("views"),
        "forwards": m.get("forwards"),
        "is_edit": False,
        "links": links,
        "media": media,
        "raw": m,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--author-name")
    ap.add_argument("--lang")
    ap.add_argument("--skip-existing", action="store_true",
                    help="read the DB (read-only) and POST only messages not yet in it")
    a = ap.parse_args()
    have: set[str] = set()
    if a.skip_existing:
        import sqlite3
        db = sqlite3.connect(f"file:{Path(__file__).resolve().parents[2] / 'record-store' / 'records.db'}?mode=ro", uri=True)
        chan = json.loads(open(a.path, encoding="utf-8").readline())["channel"]
        have = {r[0] for r in db.execute("select source_id from records where kind='post' and source=?", (f"telegram:{chan}",))}
        db.close()
    scraped_at = datetime.fromtimestamp(os.path.getmtime(a.path), timezone.utc)
    new = dup = err = 0
    with open(a.path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            m = json.loads(line)
            if str(m["id"]) in have:
                dup += 1
                continue
            try:
                r = sc.create("post", f"telegram:{m['channel']}", str(m["id"]),
                              to_data(m, scraped_at, a.author_name, a.lang))
                new += r["new"]
                dup += not r["new"]
            except Exception as e:  # noqa: BLE001
                err += 1
                print(f"line {n} id {m.get('id')}: {e}", file=sys.stderr)
            if n % 2000 == 0:
                print(f"{n} lines: new={new} dup={dup} err={err}", flush=True)
    print(f"done: new={new} dup={dup} err={err}")


if __name__ == "__main__":
    main()
