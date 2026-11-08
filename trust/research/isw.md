# ISW as ground truth: data access (tested 2026-11-08)

Tags: [Certain] = tested in this session. [Likely] = strong evidence. [Guessing] = not tested.
Scratch files: `/tmp/claude-1001/-home-vince-Vincent/ada349be-30b0-448a-be47-bb27e756a129/scratchpad/isw_dl/`.

## 1. Answer first

- **ISW polygon history exists as daily snapshots on ISW's own public ArcGIS server.** [Certain]
  The "timelapse" feature services have one polygon set per day, with a `datetime` date field.
  - Daily coverage: **2022-02-24 → 2026-05-31**. 2022: 311 of 311 days. 2023–2025: every day. [Certain]
  - **Gap: 2026-06-01 → 2026-11-06.** No timelapse service exists for this period yet. [Certain]
    Partial fill: Wayback has 3 copies of the live web map with embedded polygons (2026-09-24, 2026-10-25, 2026-11-02). [Certain]
- **The live map (current state only) is not a FeatureServer.** The StoryMap web map embeds the polygons in the item JSON. Every feature has `pub_date` = `2026-11-07`. [Certain]
- **ISW report text:** understandingwar.org and criticalthreats.org return **HTTP 403 (Cloudflare)** to this GCP VM for every path, including wp-json, RSS, sitemap and robots.txt. [Certain]
  **The Wayback Machine has almost all reports and serves them** (`id_` raw mode). [Certain]
- **License:** the web map item says: *"This geodata is the exclusive intellectual property of the Institute for the Study of War (ISW). You may not use this geodata without the written consent of ISW."* [Certain]
  Use it for internal validation only. Do not redistribute the polygons in the public demo. Ask ISW for consent. [Likely]

## 2. ArcGIS endpoints

Base: `B=https://services5.arcgis.com/SaBe5HMtmnbqSWlu/arcgis/rest/services` (ISW org; owner `gbarros_understandingwar`).
The services directory `$B?f=json` lists 326 public services. No token is necessary. `maxRecordCount` = 2000. Output formats: json, geojson, pbf. [Certain]

### 2a. History (the useful part)

| Period | Service / layer (Assessed Russian control) | Date field | Days |
|---|---|---|---|
| 2022-02-24 → 2024-12-31 | `UkraineCoTTimelapse_Full_Length_view/0` (7,540 features) | `datetime` | 1041 |
| same | `.../1` Assessed Russian Advances (56,653 features) | `datetime` | 1035 (from 2022-03-02) |
| 2023-02-01 → 2024-12-31 | `.../2` Claimed Russian Territory (17,776 features) | `datetime` | 360 (sparse) |
| same | `.../3` Claimed Ukrainian Counteroffensives (query returned error 400; uses `FID`) | `datetime` | not tested |
| 2025-01 | `JAN2025Timelapse_Ukraine_Russia_War/1` | `datetime` | 31 |
| 2025-02 | `Timelapse_FEBUkraine/29` | `datetime` | 28 |
| 2025-03 | `March_PreppedTimelapse/31` | `datetime` | 31 |
| 2025-04 | `Ukraine_COT_Timelapse_April_2025/110` | `datetime` | 30 |
| 2025-05 | `May_2025_Timelapse/3` | `datetime` | 31 |
| 2025-06 | `June_2025_Timelapse/3` | `datetime` | 30 |
| 2025-07 | `July_2025_Timelapse/3` | `datetime` | 31 |
| 2025-08 | `August_2025_Timelapse/2` | `datetime` | 31 |
| 2025-09 | `SEP2025RUCoT/5` | `datetime` | 30 |
| 2025-10 | `Russo_Ukraine_War_October_2025_Timelapse/3` | `datetime` | 31 |
| 2025-11 | `Russo_Ukraine_War_November_2025_Timelapse/33` | `datetime` | 30 |
| 2025-12 | `Ukraine_Time_Lapse_December_2025_WFL1/9` | `datetime` | 31 |
| 2026-01 | `COT_Merge_Jan_2026_view/37` | `datetime` | 31 |
| 2026-02 | `Ukraine_Timelapse_Feb_2026_WFL1_view/28` | `datetime` | 28 |
| 2026-03 | `Export_March_2026_V4/33` (315 features) | `datetime` | not counted, extent 03-01→03-31 |
| 2026-04 | `Ukraine_Timelapse_April_2026_WFL1/33` | `datetime` | 30 |
| 2026-05 | `May_CoT_view/0` (OID is `uid`) | `pub_date` (DateOnly) | 31 |

Each monthly service has sibling layers for Advances, Claimed Russian Territory, Claimed Ukrainian Counteroffensives and (from about 2025-11) Infiltration Areas. Example: `Ukraine_Timelapse_April_2026_WFL1` layers 62 = Advances, 30 = Claimed, 31 = UA CO, 64 = Infiltration. For May 2026, the sibling services are `Assessed_Russian_Advances_in_May_2026_view`, `Russian_Claimed_CoT_in_May_2026_view`, `Claimed_Ukrainian_Counteroffensives_in_May_2026_view` and `Assessed_Russian_Infiltration_Areas_in_May_2026_view`. [Certain]
Full layer scan (970 rows, service/layer/name/time field/extent): `isw_dl/layer_scan.tsv`.

Fields of `UkraineCoTTimelapse_Full_Length_view/0`: `OBJECTID, datetime (Date, the snapshot day), CreationDa, EditDate, area, Shape__Area, Shape__Length`. Also left-over admin columns (`ADM1_EN`, ...). The monthly layers have `OBJECTID, datetime, Area_KM, CreationDa, EditDate, Creator, Shape__Area`. [Certain]
Each day is the **full assessed area for that day**, not a delta. Sum of Shape__Area: 2022-03-15 is larger than 2023-06-15, which is ≈ 2024-06-15 < 2024-12-31. [Certain]
Correction: the layer **includes** Crimea and pre-2022 occupied Donbas. Point tests on 2024-06-15 for Donetsk, Simferopol, Mariupol and Luhansk all returned inside. [Certain] The live web map shows those areas as a separate layer, `VIEW_Russian_controlled_Ukrainian_Territory_before_February_24_2022/36` (2 features). The embedded web-map rings are lon/lat, not Web Mercator. [Certain]

### 2b. Current state

| What | Where | Fields |
|---|---|---|
| Live map, all layers, current day | `https://www.arcgis.com/sharing/rest/content/items/9f04944a2fe84edab9da31750c2b15eb/data?f=json` (4.3 MB; `operationalLayers[].featureCollection`) | `OBJECTID, pub_date` (string `2026-11-07`) |
| Layers in it | Assessed Russian Advances (49), Claimed Russian Territory (72), Assessed Russian-Controlled Terrain (9), Claimed Ukrainian Counteroffensives (70), Assessed Russian Infiltration Areas (187), pre-2022 (2) | |
| Older hosted views (edited last 2026-09-29, no history) | `VIEW_RussiaCoTinUkraine_V3/49`, `View_ClaimedRussianTerritoryinUkraine_v3/49` (edited last 2025-12-21), `View_AssessedRussianInfiltrationAreasinUkraine_V4/0`, `VIEW_ClaimedUkrainianCounteroffensives_V2/51` | `OBJECTID, EditDate, CreationDate` |
| Settlements gazetteer | `Ukrainian_Settlements_Updated_view/0` (29,657 points) | `ADM4_EN/UA … ADM1_EN/UA` |

StoryMap `36a7f6a6…` → single web map `9f04944a2fe84edab9da31750c2b15eb` ("Assessed Control of Terrain in Ukraine", modified 2026-11-07 21:49 UTC). [Certain]

### 2c. Working curl commands [Certain]

```bash
B=https://services5.arcgis.com/SaBe5HMtmnbqSWlu/arcgis/rest/services
L=$B/UkraineCoTTimelapse_Full_Length_view/FeatureServer/0
# attribute test
curl -s "$L/query?where=1=1&outFields=*&returnGeometry=false&resultRecordCount=5&f=json"
# list snapshot dates with feature counts
curl -s "$L/query?where=1%3D1&groupByFieldsForStatistics=datetime&outStatistics=%5B%7B%22statisticType%22%3A%22count%22%2C%22onStatisticField%22%3A%22OBJECTID%22%2C%22outStatisticFieldName%22%3A%22n%22%7D%5D&f=json"
# one day as GeoJSON, WGS84, ~10 m precision (1.0 MB; 2.0 MB at full precision; 1.5 s)
curl -s "$L/query?where=datetime%20%3D%20DATE%20%272024-06-15%27&outFields=OBJECTID,datetime&outSR=4326&geometryPrecision=4&f=geojson" -o isw_2024-06-15.geojson
# May 2026 (DateOnly field)
curl -s "$B/May_CoT_view/FeatureServer/0/query?where=pub_date%3DDATE%20%272026-05-31%27&outFields=*&outSR=4326&f=geojson"
# live web map (current day, all layers)
curl -s "https://www.arcgis.com/sharing/rest/content/items/9f04944a2fe84edab9da31750c2b15eb/data?f=json" -o webmap_$(date +%F).json
# Wayback copies of the live web map
curl -s "https://web.archive.org/cdx/search/cdx?url=www.arcgis.com/sharing/rest/content/items/9f04944a2fe84edab9da31750c2b15eb/data*&output=json&fl=timestamp,length&filter=statuscode:200&collapse=digest"
```

### 2d. Third-party copies
- Not found: GitHub, Kaggle, Zenodo or HDX repos with daily ISW polygons. GitHub search was unauthenticated, so it was shallow. [Likely]
- `longlinecode/russo-ukrainian-front-daily`: DeepState daily + 16 ISW-based "reconstructed" key dates, schematic. Not usable as truth. [Certain]
- Not needed: ISW's own server is the best source.

## 3. ISW campaign assessment text

- URL pattern (current): `https://understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment-<month>-<d>-<yyyy>/`.
  Old `www.understandingwar.org/backgrounder/russian-offensive-campaign-assessment-<month>-<d>[-<yyyy>]` returns 301 to the new path. 2022 slugs have no year (`...-assessment-april-12`). Some slugs have a double hyphen (`assessment--october-22-2025`). [Certain]
- Direct fetch: **403 Cloudflare** from this VM for HTML, `/wp-json/wp/v2/posts`, `/feed/` and `/sitemap.xml`. Criticalthreats.org is also blocked. A PDF endpoint exists (`/wp-json/grafik-pdf/v1/download/<post_id>`, post 161940 = 2026-11-06), but it is behind the same block. [Certain]
  Do not try to bypass Cloudflare. Fetching from a laptop or residential IP may work [Guessing].
- **Wayback works:** `https://web.archive.org/web/<ts>id_/<url>`. The response is gzip, so use `curl --compressed`. CDX index, current path: 735 unique dated reports (2023: 10, 2024: 57, 2025: 362, 2026: 300). Old `/backgrounder/` path: 321 undated (2022) + 2023: 360, 2024: 362, 2025: 244. Together they cover ≈ all days. [Certain]
  `curl "https://web.archive.org/cdx/search/cdx?url=understandingwar.org/backgrounder/russian-offensive-campaign-assessment*&fl=timestamp,original&filter=statuscode:200&collapse=urlkey"`
- Count: one per day since 2022-02-24 → **≈1,720 reports**. Early 2022 reports had a "Warning Update" title. [Likely]
- Size (3 tested, Oct–Nov 2026): 3,700–4,300 words of text (≈6k tokens), 80 KB gzip / 430 KB raw HTML, 47–66 endnotes. 2023–2024 reports are probably longer [Guessing].
- **Endnotes cite Telegram posts as `t.me/<channel>/<id>`** (41–58 per report: GeneralStaffZSU, wargonzo, grvZapad, kpszsu, ...). This joins ISW directly to our record store ids `telegram:<channel>`. [Certain]

### Text patterns (verbatim, from the reports of 2026-10-23, 10-29 and 11-06)
Confirmed (ISW-assessed). Typically a topline sentence, then an evidence sentence:
1. "Russian forces advanced southeast of Dobropillya."
2. "Russian forces recently advanced and conducted infiltration missions south and southeast of Dobropillya."
3. "Ukrainian forces advanced in northwestern Kostyantynivka, according to geolocated footage published on November 5."
4. "Ukrainian forces recently advanced in northern Sofiivka, according to geolocated footage published on October 22."
5. "Russian forces are operating in southern Dobropillya, according to geolocated footage published on October 22, after what ISW assesses was a Russian infiltration mission."

Negative / contradiction (useful for marking claims false):
- "Russian forces continued offensive operations in the Kupyansk direction on October 22 and 23 but did not make confirmed advances."
- "... according to geolocated footage posted on October 28, indicating that Ukrainian forces maintain positions in the area contrary to Russian claims."
- "... contrary to Russian President Vladimir Putin's claim that Russian forces seized the settlement in September 2026."

Claimed (unconfirmed):
1. "Russian milbloggers claimed on November 5 and 6 that Russian forces advanced in western Rai-Oleksandrivka (southeast of Slovyansk)."
2. "A Russian milblogger claimed on October 29 that Russian forces entered Tokarivka (north of Kharkiv City)."
3. "A Russian milblogger claimed that Russian forces seized Sobolivka (just west of Kupyansk)."
4. "A Russian milblogger claimed on October 23 that Russian forces advanced south of Lukyanivske (west of Orikhiv)."
5. "A source reporting on the Russian Western Grouping of Forces claimed that Russian forces do not control Kucherivka, Petropavlivka (both east of Kupyansk), ..."

Regex anchors: confirmed = `(Russian|Ukrainian) forces (recently )?advanced` + `according to geolocated footage published on <date>`. Claimed = `(milblogger|milbloggers|source|Russian Ministry of Defense \(MoD\)) claimed (on <date>)? that`. Negative = `did not make confirmed advances` / `contrary to (Russian )?claims`. Each sentence ends with `[n]`, which maps to an endnote URL. [Certain for the 3 reports read]

## 4. Terms and rate limits
- Geodata: "exclusive intellectual property of ISW … not without written consent" (see §1). [Certain]
- ArcGIS Online: no throttling seen at ~1 req/s. 2000 features/page; use `resultOffset` if `exceededTransferLimit`. [Certain for what was run]
- Wayback: unofficial limit about 15 req/min on some endpoints, with 429 or blocks if you go faster [Guessing]. Use 1 req per 4 s, retry on 429/5xx.
- ISW site: Cloudflare-blocked from GCP. Report text is free to read. Cite ISW. Do not republish full text. [Likely]

## 5. Recommended ingestion (next 10 h)

| Step | What | How | Time |
|---|---|---|---|
| 1 | Polygons 2022-02-24 → 2026-05-31, layer "Assessed Russian control" (+ Advances, Claimed) | Loop over days. `where=datetime=DATE 'YYYY-MM-DD'`, `outSR=4326`, `geometryPrecision=4`, `f=geojson`. Store as `trust/data/isw/<layer>/<date>.geojson` (not in records.db; derived/truth) | ≈1,550 days × 1.5 s ≈ 40 min, ≈1.5 GB at p4. Add `maxAllowableOffset=0.0005` to cut size. |
| 2 | Per-settlement truth | Point-in-polygon of settlements (ISW `Ukrainian_Settlements_Updated_view`, or ours) per day. Output `isw_truth(settlement, date, assessed, claimed, advance)` table in `trust.db` | 20–30 min with shapely STRtree |
| 3 | Gap 2026-06 → 11-07 | 3 Wayback web-map copies + live web map now. Start a daily cron that saves `webmap_<date>.json` | 10 min |
| 4 | Report text | CDX list → fetch `id_` via Wayback at 1 per 4 s. Store as `article` records (`source=isw`, `source_id=<yyyy-mm-dd>`, raw HTML in `data.raw`) through `store_client.py` | ≈1,720 × 4 s ≈ 2 h, background tmux |
| 5 | ISW claim labels | Regex on the patterns above → confirmed / claimed / negative per settlement + date + endnote URL. Join `t.me/<ch>/<id>` to our telegram records = direct "ISW confirmed/contradicted this post" labels | 1–2 h dev |

Priority if time is short: steps 1 + 2 (truth polygons) first. Step 5 has the most value for the trust layer ("ISW cited this Telegram post as a claim, then confirmed or contradicted it").
No-leakage rule: label claims at date D with the ISW snapshot from date ≥ D. The `datetime` field is the assessment date. Snapshots may be edited after the fact, because "Repair..." layers exist [Likely].

## 6. Built 2026-11-08 (steps a, b, d)
- `trust/fetch_isw.py` → `trust/data/isw/{control,advance,infil}/<date>.geojson` (1558 / 1552 / 181 days, 670 MB), `webmap/` (live + 3 Wayback copies), `pre2022.geojson`. [Certain]
  Pitfall: `maxAllowableOffset` breaks some layers (error 400, or a non-JSON reply `{ message: { code: 200 }}`), so it is used on control only. The May 2026 infiltration view hides `pub_date`, so it is skipped. [Certain]
- `trust/build_isw_truth.py` → `trust/data/isw_truth.npz` (dates/gids identical to truth.npz; state int8; have bool). Run time 3.7 min. [Certain]
  have = 1522 of 1679 days. Missing: 2024-03-31 (ISW layer empty), and 2026-06-01 → 2026-11-06 except 09-23, 10-24, 11-01 (and 11-07, live).
- Agreement with DeepState (settlements with |dist| < 10 km, have days, RU vs not-RU): **0.873** overall. By year: 2022 0.797, 2023 0.879, 2024 0.936, 2025 0.903, 2026 0.835. [Certain]
  The main disagreement is DeepState RU while ISW is not control. In 2022–2024, ISW mostly had no polygon there (-1). In 2025–2026, it is mostly ISW "advance/infiltration" (0). ISW is **more conservative than DeepState near the line**, which contradicts the "ISW overstates" note in §2d. [Certain for these counts]
- `trust/fetch_isw_text.py` (tmux `isw-text`) → `trust/data/isw/reports/<date>.html|.txt` + `index.tsv`. 1383 of 1407 days (2023-01-01 → 2026-11-07) have captures. About 11 reports/min, so it takes about 2.1 h. [Certain]
