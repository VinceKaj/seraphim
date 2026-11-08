---
name: query-records
description: Read-only SQL queries over the Seraphim record store (SQLite at record-store/records.db) - scraped Telegram/X/Bluesky posts, news articles, Polymarket snapshots, extracted claims, their links/backlinks, and source labels. Use when asked to look up, count, search, aggregate or inspect scraped records, find what a post links to or what links to it, find unresolved forward targets (channels to scrape next), check what a channel posted, trace claims to evidence, or check market prices.
---

# Query the record store with SQL

Run SQL with the read-only helper. Run it from the repo root (`seraphim/`):

```bash
python3 .claude/skills/query-records/query.py "SELECT ..."          # table, max 200 rows
python3 .claude/skills/query-records/query.py -f json "SELECT ..."  # json | jsonl | csv | table
python3 .claude/skills/query-records/query.py -n 1000 -t 60 "..."   # row cap, timeout (s)
python3 .claude/skills/query-records/query.py -w 0 "..."            # no cell truncation
python3 .claude/skills/query-records/query.py --schema              # schema + row counts
```

The helper opens the DB with `mode=ro` + `PRAGMA query_only=ON`. Writes fail. Do not write to the DB by any other means (no `sqlite3` CLI writes, no Python writes). Records are immutable; new data goes in only through the HTTP API (`record-store/store_client.py`).

Rules:
- Start with `--schema` if you are not sure what data exists.
- Always put a `LIMIT` on exploratory queries. Select specific `data ->> '$.field'` paths, not the full `data` column (`raw` can be large).
- Ignore rows with `source LIKE 'test:%'`. They are test data.
- Show the user the SQL you ran, together with the result.

## Tables

```sql
records(seq INTEGER PK AUTOINCREMENT,  -- insert order; use as cursor
        id TEXT UNIQUE,                -- sha256(key)[:16]
        key TEXT,                      -- "kind|source|source_id"
        kind TEXT,                     -- post | article | market | claim
        source TEXT, source_id TEXT,
        created_at REAL,               -- unix time of insert into the store
        data TEXT)                     -- validated JSON (see below)
links(from_id TEXT, type TEXT, target TEXT, target_id TEXT)   -- PK(from_id,type,target)
  -- type: reply | forward | quote | album | thread | url | edit_of | evidence
  -- target: a record key, a URL (type=url), or a record id (type=evidence)
  -- target_id: sha256(target)[:16] for record keys; = target for evidence; NULL for URLs
sources(author TEXT PK, data TEXT, updated_at REAL)   -- free JSON: side, reliability, notes...
```
Indexes: `records(kind, seq)`, `records(source)`, `records.id` (unique), `links(target_id)`, `links(from_id, ...)` (PK).

## Conventions

| What | kind | source | source_id |
|---|---|---|---|
| Telegram message | post | `telegram:<channel>` | `<message_id>` |
| Telegram edit | post | `telegram:<channel>` | `<message_id>@<edit_unix>` (`is_edit`=true, link `edit_of`) |
| Telegram album | post | `telegram:<channel>` | link `album` → virtual key `post\|telegram:<ch>\|album:<grouped_id>` |
| X tweet | post | `x:<handle>` | `<tweet_id>` |
| Bluesky | post | `bsky:<did>` | `<rkey>` |
| Article | article | `rss:<slug>` / `web:<domain>` | `<canonical_url>` |
| Polymarket | market | `polymarket` | `<market_id>@<snapshot_unix>` |
| Claim | claim | `extractor-<version>` | `<parent_record_id>:<n>` |

## `data` JSON fields

- **post**: `url, author, author_name, posted_at, scraped_at, lang, text, links[{type,target}], media[{type,ref}], raw, views, forwards, is_edit`
- **article**: the post envelope fields + `title, publisher, byline`
- **market**: `market_id, question, prices{"Yes":0.23,...}, volume, snapshot_at, raw`
- **claim**: `text, evidence[{record, span}], links[]`, plus extractor-specific extra fields (the schema is still changing; check `json_each(data)` to see which keys exist)

Datetimes are ISO-8601 UTC strings ending in `Z`, with optional microseconds (`2026-11-07T09:00:00Z`, `2026-11-07T09:00:00.722791Z`). String comparison sorts them by time. Use `substr(x,1,13)` for the hour and `substr(x,1,10)` for the day. `author` matches `sources.author`.

JSON access: `data ->> '$.text'` (SQL value), `data -> '$.links'` (JSON), `json_each(data, '$.evidence')` (array to rows), `json_extract(data,'$.prices.Yes')`.

To get a record id from a key, SQLite has no sha256, so compute it in Python:
`python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])" 'post|telegram:rybar|123'`
Or look it up: `SELECT id FROM records WHERE key = '...'` (only works for records that exist).

## Recipes

```sql
-- Overview: records per kind and source
SELECT kind, source, COUNT(*) n, MAX(data->>'$.posted_at') latest
FROM records WHERE source NOT LIKE 'test:%' GROUP BY kind, source ORDER BY n DESC LIMIT 50;

-- Latest posts from one channel
SELECT seq, source_id, data->>'$.posted_at' posted, data->>'$.views' views, substr(data->>'$.text',1,200) text
FROM records WHERE source='telegram:rybar' AND kind='post' ORDER BY data->>'$.posted_at' DESC LIMIT 20;

-- Text search, last 24 h. LIKE ignores case only for ASCII: for Cyrillic, search both cases
SELECT id, source, data->>'$.posted_at' posted, substr(data->>'$.text',1,200) text
FROM records WHERE kind='post' AND data->>'$.posted_at' >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')
  AND (data->>'$.text' LIKE '%Покровськ%' OR data->>'$.text' LIKE '%Pokrovsk%')
ORDER BY posted DESC LIMIT 50;

-- Posts per hour per source (activity bubbles)
SELECT substr(data->>'$.posted_at',1,13) hour, source, COUNT(*) n
FROM records WHERE kind='post' GROUP BY hour, source ORDER BY hour DESC, n DESC LIMIT 200;

-- Channels to scrape next: unresolved forward targets, grouped by channel
SELECT substr(l.target, 6, instr(substr(l.target,6),'|')-1) channel, COUNT(*) refs, COUNT(DISTINCT l.target) posts
FROM links l WHERE l.type='forward' AND NOT EXISTS (SELECT 1 FROM records r WHERE r.id=l.target_id)
GROUP BY channel ORDER BY refs DESC LIMIT 50;

-- Who forwarded / replied to / quoted a record (backlinks)
SELECT l.type, r.source, r.id, r.data->>'$.posted_at' posted
FROM links l JOIN records r ON r.id=l.from_id WHERE l.target_id=:id ORDER BY posted;

-- Outgoing links of a record, with resolution
SELECT l.type, l.target, l.target_id, EXISTS(SELECT 1 FROM records r WHERE r.id=l.target_id) resolved
FROM links l WHERE l.from_id=:id;

-- Album members: target is the virtual album key
SELECT r.id, r.source_id, r.data->'$.media' media FROM links l JOIN records r ON r.id=l.from_id
WHERE l.type='album' AND l.target='post|telegram:<ch>|album:<grouped_id>';

-- Edit history of a message (original + edits)
SELECT r.source_id, r.data->>'$.is_edit' is_edit, substr(r.data->>'$.text',1,200) text FROM records r
WHERE r.key='post|telegram:<ch>|<msg_id>'
   OR r.id IN (SELECT from_id FROM links WHERE type='edit_of' AND target='post|telegram:<ch>|<msg_id>')
ORDER BY r.seq;

-- Claims with their evidence spans and source records
SELECT c.id claim, c.data->>'$.text' claim_text, e.value->>'$.span' span, s.source, s.data->>'$.posted_at' posted
FROM records c, json_each(c.data,'$.evidence') e LEFT JOIN records s ON s.id = e.value->>'$.record'
WHERE c.kind='claim' ORDER BY c.seq DESC LIMIT 50;

-- Claims that cite a given record
SELECT r.id, r.data->>'$.text' claim_text FROM links l JOIN records r ON r.id=l.from_id
WHERE l.type='evidence' AND l.target_id=:id;

-- Latest Polymarket snapshot per market
SELECT data->>'$.question' q, data->>'$.prices' prices, data->>'$.snapshot_at' at FROM records r
WHERE kind='market' AND seq = (SELECT MAX(seq) FROM records r2 WHERE r2.kind='market'
                               AND r2.data->>'$.market_id' = r.data->>'$.market_id') LIMIT 100;

-- Price history for one market
SELECT data->>'$.snapshot_at' at, json_extract(data,'$.prices.Yes') yes FROM records
WHERE kind='market' AND data->>'$.market_id'=:market_id ORDER BY at;

-- Join source labels (side / reliability)
SELECT r.source, s.data->>'$.side' side, s.data->>'$.reliability' rel, COUNT(*) n
FROM records r LEFT JOIN sources s ON s.author = r.data->>'$.author'
WHERE r.kind='post' GROUP BY r.source ORDER BY n DESC LIMIT 50;

-- Inspect the raw platform JSON of one record
SELECT data->'$.raw' FROM records WHERE id=:id;
```

The helper does not bind parameters. Before you run a recipe, replace `:id`, `:market_id` and the `<...>` placeholders with literal quoted values.
