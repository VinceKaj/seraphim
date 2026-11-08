# Seraphim

Seraphim are the highest rank of angels in Abrahamic religions. In our project, they represent the eyes that see the world from up above and allow us to report live, minute-by-minute tracking and predictions surrounding the Ukrainian-Russian war.

Our project monitors public telegram channels, X accounts, and traditional media to build a knowlegde base of events and reporting related to Ukraine, Russia and the surrounding region. 
## What it does

We pulled **1.7M+ Telegram messages, Bluesky posts and news articles** (69k sources, Russian, Ukrainian and international) about the front. An LLM pipeline (LangGraph + Claude Haiku) reads every post that names a front-line settlement and extracts structured claims ("Russia entered Varvarivka", "Ukraine repelled an attack at Kozacha Lopan"). Each claim cites the exact sentence it came from — a claim whose evidence is not an exact substring of its post is rejected.

- **Claims on a map**, from both sides, coloured by credibility.
- **Source trust** from track record: each checkable claim is compared with DeepState's daily front line in the 90 days after it was posted (no hindsight). Sources whose claims happen gain trust; sources whose claims never happen lose it.
- **Forecast**: P(Russian control) for 7,542 settlements at +7 / +30 / +90 days (walk-forward AUC 0.95–0.98).
- **vs Polymarket**: across 337 settled territorial markets, Seraphim beats Polymarket on 273 (lower squared error over the market's life). Example: Polymarket priced "Russia captures Kostyantynivka by 31 Mar 2026" above 50% for 83 days (peak 88%); Seraphim stayed under 5% for most of it; it resolved No.
- **Validation page** with the weak spots too: pooled over all market-days Polymarket's error is lower (0.089 vs 0.132), and it wins at ≤30 days, because it reacts to ISW infiltration updates our geometry model does not see.

## Layout

| Path | What |
|---|---|
| `record-store/` | Immutable record store (SQLite + FastAPI on 127.0.0.1:8100); every scraper writes here |
| `scrapers/` | Telegram (t.me preview, Telethon dump import), Bluesky, ISW URL list |
| `trust/` | Truth tables (DeepState / ISW → per-settlement state per day), prefilter, LLM extraction (`extract.py`), prompt eval harness, forecaster, Polymarket comparison, exports for the app |
| `app/` | Demo map (MapLibre, built on Kacper's map) + `stats.html` (scale, validation, source trust) |
| `tools/` | Terminal dashboard |

## Rebuild the demo data

```bash
uv run trust/build_truth.py --geonames <dir>      # DeepState polygons -> trust/data/truth.npz, gazetteer in trust/trust.db
uv run trust/prefilter.py --since 2026-10-08 --until 2026-11-09
uv run trust/extract.py --all --since 2026-10-08 --until 2026-11-09 --max-cost 4   # Haiku, ~$0.0002 / post
uv run trust/forecast.py                          # walk-forward backtest
uv run trust/pm_compare.py                        # vs Polymarket (needs trust/data/pm.duckdb)
uv run trust/export_demo.py && uv run trust/export_extras.py && uv run trust/export_validation.py
cd app && npx vite --host 127.0.0.1 --port 5180   # see app/README.md
```

Data files (`trust/data/`, `trust/trust.db`, `record-store/records.db`) are not in git. ISW-derived data stays internal (validation only) until we have written permission. The satellite-damage layer in the demo is **simulated** (placeholder for the building-damage pipeline) and labelled as such in the UI.
