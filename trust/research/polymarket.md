# Polymarket: territorial and strike markets (research, 2026-11-08)

Data: `/home/vince/Huge/potato patato/` (read-only). Working files: `/tmp/claude-1001/-home-vince-Vincent/ada349be-30b0-448a-be47-bb27e756a129/scratchpad/pm/`
(`build.py` builds the CSV, `pm.duckdb` holds the full price history, `api/` holds the API test responses).
Output CSV: `trust/research/pm_territorial.csv` (454 rows).

## 1. How capture/control markets resolve

The regex `Russia (capture|control|recapture|take|enter)` gives 455 markets. 1 is a false positive ("Will Russia take second place in Miss Universe"), so 454 are territorial.

**The resolution source is the ISW map, not DeepState.** [Certain]

| Resolution chain (from `description`) | Markets |
|---|---|
| ISW map > DeepStateMap (if ISW is unavailable) > "consensus of credible reporting" | 453 |
| ISW map > Liveuamap > consensus (old 2024 Chasiv Yar market) | 1 |

The `resolutionSource` field is empty on 277 markets. It is the ISW storymap or a Google Maps pin on the others. The `description` is the real rule.

**What counts as "capture"** depends on the question wording [Certain, regex over all 454 descriptions]:

| Question form | Rule in description | Markets |
|---|---|---|
| "capture X" | **One landmark** (a church, a railway station, an intersection or a coordinate) has *any part* shaded on ISW | 211 |
| "capture all of X" | The **entire municipality** is shaded (a thin grey edge is OK) | 75 |
| "enter X" | **Any part** of the settlement is shaded. Infiltration areas count. | 158 |
| "recapture X" (Sudzha) | Landmark rule | 5 |
| "control X on date" | Landmark is red *on* the date (not "by") | 1 |

Rules that change the result:
- Newer markets (377): the shading must **persist through the next full ISW daily update**.
- Infiltration layer: it counts for all "enter" markets. It is **excluded** for 118 "capture" and 58 "capture all" markets ("Assessed Russian Infiltration Areas" and "Claimed Russian Territory" do not qualify).
- "Once a qualifying condition is met, any subsequent loss of control will not be considered".
- Negotiated settlement: real control counts, a de-jure announcement does not. The oblast-level markets need military conquest.

Verbatim excerpts (3 representative rules):

> **Will Russia capture Pokrovsk by October 31?** "This market will resolve to "Yes" if, according to the ISW map, Russia captures St. Michael's church in Pokrovsk located on Hrushevskoho Vulytsia by October 31, 2025, 11:59 PM ET. The church will be considered captured if any part of building is shaded red on the ISW map (https://storymaps.arcgis.com/stories/36a7f6a6f5a9448496de641cf64bd375) by the resolution date. [...] The primary resolution source for this market will be the ISW Ukraine map. If the ISW map is rendered unavailable, information from DeepStateMap (https://deepstatemap.live/) may be used. If information from both the ISW and DeepStateMap are rendered permanently unavailable, a consensus of credible reporting may be used."

> **Will Russia capture all of Pokrovsk by August 31?** "Pokrovsk will be considered captured if the entirety of the municipality is shaded red on the ISW map [...] If all area within the municipality is shaded in red, however the shading does not precisely match up with the border such that there is a tiny amount of grey along the border of the city, this will still qualify for a "Yes" resolution."

> **Will Russia enter Orikhiv by May 31?** "Territory will be considered captured if any part of Orikhiv is shaded under a below specified layer on the ISW map [...] the relevant shading indicating Russian control must persist through the next full ISW daily update cycle. [...] Any continuous shading which reflects either "Assessed Russian Infiltration Areas in Ukraine", "Assessed Russian Control", "Assessed Russian Advance In Ukraine", or "Assessed Russian Gains in the Past 24 Hours" will qualify."

Outcomes: 69 Yes ($17.0M volume), 298 No ($42.9M), 86 unresolved ($6.7M), 1 other No. The CSV covers 80 distinct settlement names.

**Consequence for Seraphim:** the thing to predict is **"ISW shades landmark L"**, not "DeepState polygon covers town". The trust layer must keep DeepState as truth, but a market signal needs the ISW map as a second label. [Certain]

## 2. Strike markets

Regex hits for strike/attack/drone/missile/hit/explosion/sabotage: 204. Esports ("Counter-Strike"), Miss Universe and nuclear-deal markets are removed. About 155 real strike markets remain:

| Family | Markets | Resolved Yes | Volume |
|---|---|---|---|
| Russia strike Kyiv municipality, daily ("on Feb 5, 2026") and "by date" | 69 | 16 | $3.24M |
| Russia strike impacts Kyiv, weekly (Mar to Apr 2026) | 9 | ? | - |
| Ukraine strikes another tanker / vessel in Black Sea; Russia strikes vessel | 15 + 12 | 5 + ? | $3.33M (tanker) |
| Russian strike on Poland / a NATO member | 6 | 0 | $3.72M |
| NATO downs another Russian drone | 15 | 4 | $0.88M |
| Ukraine strike in Russia (daily) | 11 | 3 | $0.21M |
| Ukraine strike on Moscow (municipality) | 3 | 0 | $0.31M |
| Crimean bridge hit | 5 | 0 | $0.32M |
| Other: ATACMS/Tomahawk, ZNPP explosion (3), EU blames Russia for sabotage (6), Iran strike Ukraine | ~13 | - | - |

Rule (verbatim, **Will Russia strike Kyiv on February 5, 2026?**):

> "This market will resolve to "Yes" if the Russian Armed Forces initiate a drone, missile, or air strike on Kyiv municipality on the listed date Eastern European Time (EET). [...] a qualifying "strike" is defined as the use of aerial bombs, drones, or missiles (including cruise or ballistic missiles) launched by Russian Armed Forces that impact Kyiv municipality's ground territory. [...] Missiles or drones that are intercepted and surface-to-air missile strikes will not be sufficient for a "Yes" resolution [...] The primary resolution source will be official government/military statements (Ukrainian or Russian), multilateral bodies (UN, etc.), or a consensus of credible reporting [...] If the date/time of a strike cannot be confirmed by a consensus of credible reporting within 48 hours of this market's end date, it will resolve to "No"".

The Kyiv daily strike markets are the best "seconds" target [Likely]. Telegram (KMVA/Klitschko, monitoring channels, `kpszsu`) reports impacts and debris in Kyiv in real time, and the market resolves on official statements. The territorial markets resolve on a once-a-day ISW map.

## 3. CSV `pm_territorial.csv`

Columns: `condition_id, question, settlement, qualifier (capture|all|any|recapture|control-on-date, +again), deadline, closed, resolved (Yes/No/unresolved), last_yes_price, resolution_source, rule, volume, closed_time, n_points, median_spacing_s, history_start, history_end, move6h_start_utc, move6h_end_utc, move6h_size, price_before, price_after, max_1step_jump, max_1step_utc, first_cross_0_5_utc, first_cross_0_9_utc`.

- `move6h_*` is the largest rise of the Yes price inside any 6 h window (low point to the later high point).
- The `deadline` comes from the date in the description. Gamma `endDate` is often wrong (example: "by December 31" with endDate 2025-06-30).
- 7 markets have no price history.

Caveats [Certain]:
- Huge's scraper fetches history only from `startDate` to `min(endDate, now)+1 day` (`pm_scraper.py` line 183). When `endDate` is too early, the resolution jump is missing. Of the 69 Yes markets, 5 have truncated history (Kupiansk Dec 31, Pokrovsk Dec 31, Myrnohrad Dec 31, Uspenivka Nov 30, Yampil' Nov 30) and 4 have no history at all (Myrnohrad all Feb 28 / Mar 31, Rodynske Mar 31, Dnipropetrovsk Oblast). To fill the gaps, fetch again from the CLOB.
- On long markets, `move6h` can be the price discovery just after creation (Kostyantynivka by Dec 31 2026: Nov 2025). For the demo, use `first_cross_0_9_utc` or the move nearest `closed_time`.
- Duplicate question texts exist across years. Always key on `condition_id`.

## 4. Latency limit

| Source | Resolution | Tested |
|---|---|---|
| `price_history.csv` | **Hourly.** Spacing p1/p50/p99 = 3576/3600/7204 s, with some long gaps. | [Certain], 5.93M rows, 3,689 markets |
| CLOB `GET clob.polymarket.com/prices-history?market=<token>&startTs&endTs&fidelity=1` | **~60 s.** Works on markets closed in Mar 2025 to Aug 2026. | [Certain] |
| Data API `GET data-api.polymarket.com/v2/trades?condition=<cid>&limit=1000` | **Per trade, 1 s timestamps** (block time) with wallet, side, size and tx hash. Pages through `pagination.next_cursor`. `start`/`end` work only with `user=`. v1 `/trades` is retired. | [Certain] |

**The hourly CSV cannot show "seconds".** It can show only which hour the market moved. Trades v2 shows the second. Measured jumps:

| Market | Minute data (fidelity=1) | Trades v2 (seconds) |
|---|---|---|
| Kupiansk by Oct 31 (2025) | 14:20 → 14:21 UTC Oct 29: 0.255 → 0.885 | 14:20:13 0.331, 14:20:21 0.396, 14:20:41 0.717, **14:20:45 0.97** |
| Pokrovsk by Aug 31 (2025) | 17:32 → 17:33 UTC Aug 8: 0.485 → 0.855 | not fetched |
| Kostyantynivka by Aug 31 (2026) | **18:04 → 18:05 UTC Aug 18: 0.215 → 0.85** | not fetched |

The price reprices in **~30 seconds**. All three jumps fall at 14:00 to 18:30 UTC, which fits a **daily ISW map update**, not the battlefield event [Guessing; check against the ISW update times]. So, for territorial markets:
- The "seconds" edge is in detecting the ISW map change itself (poll the ISW ArcGIS layers) [Guessing that the feature service can be polled].
- The Telegram edge is **hours to days** before the ISW update. We measure it as: claim time minus the jump time from trades v2.

For Kyiv strike markets, a seconds-to-minutes lead from Telegram is measurable with trades v2 [Likely].

## 5. Demo candidates: top 10 resolved-Yes by volume

Times are UTC. "6h move" comes from the hourly CSV.

| # | Market (cid in CSV) | Deadline | Vol $k | 6h move (hourly) | Before→after | First ≥0.9 | Closed |
|---|---|---|---|---|---|---|---|
| 1 | Capture Kostyantynivka by Dec 31, 2026 | 2026-12-31 | 2284 | 2025-11-13 18→00 (creation) | 0.50→0.77 | 2025-11-22 16h | 2026-08-19 21h |
| 2 | Capture Kupiansk by Oct 31 | 2025-10-31 | 1919 | 2025-10-29 14→16 | 0.22→1.00 | 2025-10-29 15h | 2025-10-29 17h |
| 3 | Capture Pokrovsk by Aug 31 | 2025-08-31 | 1798 | 2025-08-08 12→18 | 0.35→0.98 | 2025-08-08 18h | 2025-08-08 20h |
| 4 | Capture Kupiansk by Dec 31 | 2025-12-31 | 1555 | 2025-04-18 14→18 | 0.34→0.55 | (history cut 09-01) | 2025-10-29 16h |
| 5 | Capture Pokrovsk by Dec 31 | 2025-12-31 | 959 | 2025-02-03 18→22 | 0.55→0.82 | (history cut 07-01) | 2025-08-08 22h |
| 6 | Capture Kostyantynivka by Sep 30 | 2026-09-30 | 709 | 2026-08-18 17→23 | 0.62→1.00 | 2026-06-18 19h | 2026-08-19 21h |
| 7 | Capture all of Myrnohrad by Feb 28 | 2026-02-28 | 698 | no history | - | - | 2026-02-06 00h |
| 8 | Capture Kostyantynivka by Aug 31 | 2026-08-31 | 505 | 2026-08-18 14→19 | 0.21→0.99 | 2026-08-18 19h | 2026-08-19 21h |
| 9 | Enter Dobropillia by Dec 31, 2026 | 2026-12-31 | 453 | 2026-05-12 07→08 | 0.30→0.65 | 2026-08-21 15h | 2026-09-22 23h |
| 10 | Recapture Sudzha by Mar 31 | 2025-03-31 | 453 | 2025-03-08 07→09 | 0.28→0.51 | 2025-03-11 01h | 2025-03-12 20h |

**Best three for the demo** [Likely]: (2) Kupiansk on 2025-10-29 14:20 UTC, (3) Pokrovsk on 2025-08-08 17:32 UTC and (8) Kostyantynivka on 2026-08-18 18:05 UTC. Each has one clean jump to ~1.0, known to the minute. The same jump repeats in sibling markets: Pokrovsk Oct 31 and Kostyantynivka Sep 30 / Dec 31. Look for Telegram claims about the named landmark (St. Michael's church in Pokrovsk, Kupiansk-Pivdennyi railway station, Kostyantynivka railway station) in the 0 to 72 h before each jump.

## Next steps
1. Re-fetch minute history and trades v2 for the 69 Yes markets plus the Kyiv daily strike markets. This is ~140 small requests. Store them in the scratchpad or in `trust/`, not in Huge's directory.
2. Extract the landmark per market (church, station, coordinates) from the `description` into the CSV. Seraphim must give P(landmark shaded), not P(town).
3. Get the ISW map update times to confirm that the jumps follow ISW.
