# Phase 0 scout — 2026-11-08 00:25 UTC

## Record store
- 32,740 records, all `post`: `telegram:kpszsu` 31,089, `bsky:*` 1,651. No claims, articles or markets.

## Huge — `/home/vince/Huge/potato patato/` (read-only)
- `telegram_data/<channel>/messages.jsonl` (+ `.csv`), 31 channels, 815,106 messages, 1.2 GB. **Not ingested into the store.**
- Format per line: `id, date (tz-aware), channel, text, views, forwards, replies, reactions, media_type, media_path, forwarded_from, reply_to_id, grouped_id, post_author, edit_date, url`.
- War-relevant channels with capture vocabulary (освобожд/взят/звільн/контрол):

| channel | msgs | from → to | keyword hits |
|---|---|---|---|
| mod_russia | 54,532 | 2022-03-12 → 2026-11-07 | 4,886 |
| Army_Russia | 48,768 | 2017-04 → 2026-11-07 | 1,862 |
| AFUStratCom | 35,483 | 2022-03-22 → 2026-11-07 | 1,286 |
| ASGasparyan | 70,079 | 2017-07 → 2026-11-07 | 1,285 |
| A_S_Sukonkin | 10,242 | 2021-02 → 2026-11-07 | 548 |
| AG_DPR | 7,977 | 2022-03-06 → 2026-11-06 | 356 |

- Many other channels are off-topic (Spanish/Argentine/Australian/German commentary).
- `all_channels.csv`: 1,491 channels with `Language, Country, Political view` (values include `Pro-Kremlin`, `Pro-Ukraine`, `Traditional Mass Media`). Usable as the side prior.
- `loopy.py` scrapes the CSV list one channel at a time (running in tmux `loopy`). It writes to disk only.
- **Polymarket is Huge's** (`pm_scraper.py`, tmux `pm-scraper`): `markets.csv` 4,009 markets, `markets_raw.json`, `price_history.csv` 5.9 M points (856 MB, hourly).
  - 455 markets match `Russia (capture|control|recapture|take|enter)`; **369 are closed (resolved)**.
  - 156 of them cite the ISW StoryMap as resolution source; 277 have no resolution source in the CSV. Resolution is **not** DeepState.

## Kacper — `/home/vince/Kacper/` (read-only)
- `public/data/frontline/YYYY-MM-DD.geojson`: 1,590 daily files, 2022-04-04 → 2026-11-07. One MultiPolygon = Russian-held (occupied + Crimea + ORDLO). Binary only.
- `.cache/deepstate-raw/`: 742 raw DeepState versions (2022-04 → 2024-07) with `Occupied`, `Liberated`, `Unknown status` (grey zone) polygons. After 2024-07-08 (cyterat archive) there is **no grey zone**.
- H3: client-side `h3-js`, default resolution 6 (36 km²), menu 4–8. Model input format: `public/data/h3/predictions.json` (`cells: {h3: [p per horizon]}`, `horizons: [dates]`). No file exists yet.
- Join key not yet confirmed by Kacper.

## Environment
- `ANTHROPIC_API_KEY` is **not set** in the shell. No LLM work is possible until it is.
- No langchain / langgraph / h3 / shapely / numpy in the uv env yet.
- VM: 8 CPU, 31 GB RAM, 133 GB free disk. GeoNames download reachable.
