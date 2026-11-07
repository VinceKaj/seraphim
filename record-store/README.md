# Seraphim record store

One shared, validated, append-only place for every scraper and agent on this VM to write and read records.

- Service: `record-store` (systemd), listens on **http://127.0.0.1:8100** only
- DB: `record-store/records.db` (SQLite, WAL; not in git; override with env `STORE_DB`)
- Code: `schemas.py` (pydantic schemas per kind), `store.py` (FastAPI app), `store_client.py` (Python client), `test_store.py` (acceptance tests)
- Interactive API docs: http://127.0.0.1:8100/docs

```bash
record-store-attach                    # live logs in tmux session "record-store" (= ./attach.sh); detach with Ctrl-b d
journalctl -u record-store -f          # same logs without tmux
systemctl status record-store          # state
sudo systemctl restart record-store    # after editing store.py / schemas.py
cd record-store && uv run pytest test_store.py -v
```

systemd runs uvicorn directly, so it starts after a reboot and restarts after a crash. Logs go to journald. The tmux session `record-store` on the default socket only shows `journalctl -f`. `Ctrl-c` there stops the log view, not the server. `attach.sh` creates the session again if it is missing (for example after a reboot).

## Rules

1. **Records are immutable.** There is no update or delete. A correction or an edit is a new record.
2. **Deterministic ids.** `key = f"{kind}|{source}|{source_id}"`, `id = sha256(key)[:16]`. Posting the same record again is a no-op (`new: false`). You can compute the id of a record that is not scraped yet, so links can point forward.
3. **Validation at write time.** `data` is validated against the schema for `kind` (`post`, `article`, `market`, `claim`). Invalid data or unknown kind → HTTP 422. All datetimes must have a timezone; the store saves them as UTC.
4. **Always send `raw`** (the untouched platform JSON) for posts, articles and markets.
5. **Only platform-given links** at ingest: `reply`, `forward`, `quote`, `album`, `thread`, `url`, `edit_of`. A link `target` is a record key (`post|...`, `article|...`, `market|...`, `claim|...`) or, for `url`, a URL. Semantic "same event" linking happens later in the claims layer.
6. Source reliability and side labels are **not** on records. Put them in `/sources/{author}` (mutable).

## Conventions

| What | kind | source | source_id | Extra |
|---|---|---|---|---|
| Telegram message | `post` | `telegram:<channel_username>` | `<message_id>` | |
| Telegram edit | `post` | `telegram:<channel_username>` | `<message_id>@<edit_date_unix>` | `is_edit=True`, link `{"type":"edit_of","target":"post\|telegram:<ch>\|<message_id>"}` |
| Telegram album item | `post` | `telegram:<channel_username>` | `<message_id>` | link `{"type":"album","target":"post\|telegram:<ch>\|album:<grouped_id>"}` (virtual key, never resolved; backlinks on `rid(key)` give the album) |
| Telegram forward | `post` | `telegram:<channel_username>` | `<message_id>` | link `{"type":"forward","target":"post\|telegram:<orig_channel>\|<orig_msg_id>"}` |
| X tweet | `post` | `x:<handle>` | `<tweet_id>` | |
| Bluesky post | `post` | `bsky:<did>` | `<rkey>` | |
| Article | `article` | `rss:<publisher_slug>` or `web:<domain>` | `<canonical_url>` | |
| Polymarket snapshot | `market` | `polymarket` | `<market_id>@<snapshot_unix>` | |
| Claim | `claim` | `extractor-<version>` | `<parent_record_id>:<n>` | `evidence: [{record, span}]`, at least one |

`author` in the envelope is the stable author id: `telegram:rybar`, `x:GeoConfirmed`, `bsky:<did>`. Use the same string as the `/sources` key.

Test records use `source` values that start with `test:`. The test suite deletes them when it finishes. Do not use this prefix for real data.

## Endpoints (curl)

```bash
S=http://127.0.0.1:8100

# Health: count, max_seq, by_kind
curl -s $S/health

# Create a record -> {"id","key","seq","new"}
curl -s -X POST $S/records -H 'Content-Type: application/json' -d '{
  "kind": "post", "source": "telegram:rybar", "source_id": "12345",
  "data": {
    "author": "telegram:rybar", "author_name": "Рыбарь",
    "posted_at": "2026-11-07T09:12:00+00:00", "scraped_at": "2026-11-07T09:15:03+00:00",
    "lang": "ru", "text": "...", "views": 81000,
    "links": [{"type": "forward", "target": "post|telegram:dva_majors|5550"}],
    "media": [{"type": "photo", "ref": "AgACAgIAAxkB..."}],
    "raw": {"id": 12345, "message": "..."}
  }}'

# Read one record (404 if missing)
curl -s $S/records/<id>

# Tail: records with seq > since, ascending; optional kind/source; limit default 100, max 1000
curl -s "$S/records?since=0&kind=post&source=telegram:rybar&limit=100"

# Outgoing links, each with target_id and resolved
curl -s $S/records/<id>/links

# Backlinks: who links TO this id (forwards, album members, edits, claims citing it)
curl -s "$S/records/<id>/backlinks?type=forward"

# Unresolved record-key targets, most referenced first ("channels to scrape next")
curl -s "$S/unresolved?type=forward&limit=50"

# Sources (mutable metadata per author)
curl -s -X PUT $S/sources/telegram:rybar -H 'Content-Type: application/json' \
  -d '{"side": "ru", "reliability": 0.6, "notes": "milblogger"}'
curl -s $S/sources/telegram:rybar
curl -s $S/sources
```

A 422 response contains the pydantic error list in `detail`, for example:
`{"detail":[{"type":"value_error","loc":["posted_at"],"msg":"Value error, datetime must be timezone-aware", ...}]}`

## Python client

```python
import store_client as sc          # STORE_URL env var, default http://127.0.0.1:8100

k = sc.key("post", "telegram:rybar", "12345"); i = sc.rid(k)   # same hash as the server
sc.create(kind, source, source_id, data)    # data: dict or pydantic model (schemas.Post etc.)
sc.read(i); sc.links(i); sc.backlinks(i, type="forward")
sc.tail(since=0, kind="post", source=None, limit=100)
sc.unresolved(type="forward")
sc.put_source("telegram:rybar", {"side": "ru"}); sc.get_source("telegram:rybar")

for rec in sc.follow(kind="post", cursor_file="extractor.cursor"):
    ...   # tails forever; the cursor is saved after you finish each record
```

From another project directory, add `seraphim/record-store` to `PYTHONPATH` (or copy `store_client.py`; it needs only `requests`).

### Example: Telethon-style scraper (forward + album + edit)

```python
from datetime import datetime, timezone
import store_client as sc

def ingest(ch: str, m):                       # m: telethon Message, ch: channel username
    sid, links = str(m.id), []
    if m.fwd_from and m.fwd_from.channel_post:   # resolve channel id -> username as needed
        links.append({"type": "forward", "target": sc.key("post", f"telegram:{fwd_username(m)}", m.fwd_from.channel_post)})
    if m.grouped_id:
        links.append({"type": "album", "target": sc.key("post", f"telegram:{ch}", f"album:{m.grouped_id}")})
    if m.edit_date:
        sid = f"{m.id}@{int(m.edit_date.timestamp())}"
        links.append({"type": "edit_of", "target": sc.key("post", f"telegram:{ch}", m.id)})
    sc.create("post", f"telegram:{ch}", sid, {
        "url": f"https://t.me/{ch}/{m.id}", "author": f"telegram:{ch}", "posted_at": m.date,
        "scraped_at": datetime.now(timezone.utc), "text": m.message or "", "views": m.views,
        "forwards": m.forwards, "is_edit": bool(m.edit_date), "links": links, "raw": m.to_dict()})
```

Notes for this snippet:
- `m.to_dict()` can contain `bytes` and `datetime`. Convert them before you send (for example `json.loads(json.dumps(m.to_dict(), default=str))`).
- For the first ingest of a message that already has `edit_date`, also create the base record (`source_id=str(m.id)`), so that `edit_of` resolves.
- Use `sc.unresolved(type="forward")` to find channels that other channels forward from but that nobody scrapes yet.
