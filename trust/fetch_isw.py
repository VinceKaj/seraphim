"""Download ISW daily control-of-terrain polygons (ArcGIS) into trust/data/isw/.

ISW geodata is ISW intellectual property ("may not use without written consent"):
internal validation only, never republish. trust/data/ is gitignored.

Layers per day (kind):  control = Assessed Russian-controlled territory
                        advance = Assessed Russian advances
                        infil   = Assessed Russian infiltration areas (from 2025-11)
Output: data/isw/<kind>/<YYYY-MM-DD>.geojson (WGS84, ~20 m generalised), one file per day queried
        (empty FeatureCollection if ISW has nothing that day). Re-runs skip existing files.
        data/isw/webmap/<capture>.json  live web map item JSON (current day) + 3 Wayback copies.
        data/isw/pre2022.geojson        Russian-controlled territory before 2022-02-24.
Usage: uv run trust/fetch_isw.py [--workers 6] [--kinds control,advance,infil] [--no-webmap]
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "isw"
B = "https://services5.arcgis.com/SaBe5HMtmnbqSWlu/arcgis/rest/services"
WEBMAP = "https://www.arcgis.com/sharing/rest/content/items/9f04944a2fe84edab9da31750c2b15eb/data?f=json"
WAYBACK = ["20260924161033", "20261025175009", "20261102083330"]
PRE2022 = ("VIEW_Russian_controlled_Ukrainian_Territory_before_February_24_2022", 36)

# (first day, last day, kind, service, layer id, date field)
SOURCES = [
    ("2022-02-24", "2024-12-31", "control", "UkraineCoTTimelapse_Full_Length_view", 0, "datetime"),
    ("2022-03-02", "2024-12-31", "advance", "UkraineCoTTimelapse_Full_Length_view", 1, "datetime"),
    ("2025-01-01", "2025-01-31", "control", "JAN2025Timelapse_Ukraine_Russia_War", 1, "datetime"),
    ("2025-01-01", "2025-01-31", "advance", "JAN2025Timelapse_Ukraine_Russia_War", 0, "datetime"),
    ("2025-02-01", "2025-02-28", "control", "Timelapse_FEBUkraine", 29, "datetime"),
    ("2025-02-01", "2025-02-28", "advance", "Timelapse_FEBUkraine", 28, "datetime"),
    ("2025-03-01", "2025-03-31", "control", "March_PreppedTimelapse", 31, "datetime"),
    ("2025-03-01", "2025-03-31", "advance", "March_PreppedTimelapse", 36, "datetime"),
    ("2025-04-01", "2025-04-30", "control", "Ukraine_COT_Timelapse_April_2025", 110, "datetime"),
    ("2025-04-01", "2025-04-30", "advance", "Ukraine_COT_Timelapse_April_2025", 0, "datetime"),
    ("2025-05-01", "2025-05-31", "control", "May_2025_Timelapse", 3, "datetime"),
    ("2025-05-01", "2025-05-31", "advance", "May_2025_Timelapse", 1, "datetime"),
    ("2025-06-01", "2025-06-30", "control", "June_2025_Timelapse", 3, "datetime"),
    ("2025-06-01", "2025-06-30", "advance", "June_2025_Timelapse", 2, "datetime"),
    ("2025-07-01", "2025-07-31", "control", "July_2025_Timelapse", 3, "datetime"),
    ("2025-07-01", "2025-07-31", "advance", "July_2025_Timelapse", 0, "datetime"),
    ("2025-08-01", "2025-08-31", "control", "August_2025_Timelapse", 2, "datetime"),
    ("2025-08-01", "2025-08-31", "advance", "August_2025_Timelapse", 0, "datetime"),
    ("2025-09-01", "2025-09-30", "control", "SEP2025Timelapse1_WFL1", 5, "datetime"),
    ("2025-09-01", "2025-09-30", "advance", "SEP2025Timelapse1_WFL1", 2, "datetime"),
    ("2025-10-01", "2025-10-31", "control", "Russo_Ukraine_War_October_2025_Timelapse", 3, "datetime"),
    ("2025-10-01", "2025-10-31", "advance", "Russo_Ukraine_War_October_2025_Timelapse", 1, "datetime"),
    ("2025-11-01", "2025-11-30", "control", "Russo_Ukraine_War_November_2025_Timelapse", 33, "datetime"),
    ("2025-11-01", "2025-11-30", "advance", "Russo_Ukraine_War_November_2025_Timelapse", 30, "datetime"),
    ("2025-11-01", "2025-11-30", "infil", "Russo_Ukraine_War_November_2025_Timelapse", 41, "datetime"),
    ("2025-12-01", "2025-12-31", "control", "Ukraine_Time_Lapse_December_2025_WFL1", 9, "datetime"),
    ("2025-12-01", "2025-12-31", "advance", "Ukraine_Time_Lapse_December_2025_WFL1", 834, "datetime"),
    ("2025-12-01", "2025-12-31", "infil", "Ukraine_Time_Lapse_December_2025_WFL1", 854, "datetime"),
    ("2026-01-01", "2026-01-31", "control", "COT_Merge_Jan_2026_view", 37, "datetime"),
    ("2026-01-01", "2026-01-31", "advance", "ADV_merge_JAN_2026_view", 36, "datetime"),
    ("2026-01-01", "2026-01-31", "infil", "INFIL_merge_view", 35, "datetime"),
    ("2026-02-01", "2026-02-28", "control", "Ukraine_Timelapse_Feb_2026_WFL1_view", 28, "datetime"),
    ("2026-02-01", "2026-02-28", "advance", "Ukraine_Timelapse_Feb_2026_WFL1_view", 32, "datetime"),
    ("2026-02-01", "2026-02-28", "infil", "Ukraine_Timelapse_Feb_2026_WFL1_view", 29, "datetime"),
    ("2026-03-01", "2026-03-31", "control", "Export_March_2026_V4", 33, "datetime"),
    ("2026-03-01", "2026-03-31", "advance", "Export_March_2026_V4", 34, "datetime"),
    ("2026-03-01", "2026-03-31", "infil", "Export_March_2026_V4", 3, "datetime"),
    ("2026-04-01", "2026-04-30", "control", "Ukraine_Timelapse_April_2026_WFL1", 33, "datetime"),
    ("2026-04-01", "2026-04-30", "advance", "Ukraine_Timelapse_April_2026_WFL1", 62, "datetime"),
    ("2026-04-01", "2026-04-30", "infil", "Ukraine_Timelapse_April_2026_WFL1", 64, "datetime"),
    ("2026-05-01", "2026-05-31", "control", "May_CoT_view", 0, "pub_date"),
    ("2026-05-01", "2026-05-31", "advance", "Assessed_Russian_Advances_in_May_2026_view", 0, "pub_date"),
    # May 2026 infiltration: the only public view hides pub_date, so it cannot be split by day (skipped)
]
PAGE = 1000


def get(url: str, tries: int = 5) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"retry {i + 1} {e} {url[:120]}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise RuntimeError


def query(service: str, layer: int, where: str, generalise: bool = False) -> dict:
    feats = []
    off = 0
    while True:
        p = {"where": where, "outFields": "*", "outSR": 4326, "geometryPrecision": 5,
             "resultOffset": off, "resultRecordCount": PAGE, "f": "geojson"}
        if generalise:  # ~20 m; some layers reject maxAllowableOffset ("Unable to perform query"), so control only
            p["maxAllowableOffset"] = 0.0002
        q = urllib.parse.urlencode(p)
        for i in range(6):  # the server sometimes answers "{ message: { code: 200 }}" (not JSON): retry
            try:
                d = json.loads(get(f"{B}/{service}/FeatureServer/{layer}/query?{q}"))
                break
            except json.JSONDecodeError:
                if i == 5:
                    raise
                time.sleep(3 * (i + 1))
        if "error" in d:
            raise RuntimeError(f"{service}/{layer} {where}: {d['error']}")
        fs = d.get("features", [])
        feats += fs
        more = d.get("exceededTransferLimit") or (d.get("properties") or {}).get("exceededTransferLimit")
        if not fs or not (more or len(fs) == PAGE):
            break
        off += len(fs)
    return {"type": "FeatureCollection", "features": feats,
            "isw_source": {"service": service, "layer": layer, "where": where}}


def day_job(args):
    day, kind, service, layer, field = args
    out = OUT / kind / f"{day}.geojson"
    if out.exists():
        return day, kind, -1
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    try:
        fc = query(service, layer, f"{field} >= DATE '{day}' AND {field} < DATE '{nxt}'", kind == "control")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {kind} {day} {e}", file=sys.stderr, flush=True)
        return day, kind, -2
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(fc))
    tmp.rename(out)
    return day, kind, len(fc["features"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--kinds", default="control,advance,infil")
    ap.add_argument("--no-webmap", action="store_true")
    a = ap.parse_args()
    kinds = set(a.kinds.split(","))
    for k in kinds:
        (OUT / k).mkdir(parents=True, exist_ok=True)
    (OUT / "webmap").mkdir(parents=True, exist_ok=True)

    if not a.no_webmap:
        today = date.today().isoformat()
        p = OUT / "webmap" / f"live_{today}.json"
        if not p.exists():
            p.write_bytes(get(WEBMAP))
        for ts in WAYBACK:
            p = OUT / "webmap" / f"wayback_{ts}.json"
            if not p.exists():
                p.write_bytes(get(f"https://web.archive.org/web/{ts}id_/{WEBMAP.replace('?f=json', '?f=json')}"))
                time.sleep(4)
        p = OUT / "pre2022.geojson"
        if not p.exists():
            p.write_text(json.dumps(query(PRE2022[0], PRE2022[1], "1=1")))
        print("webmap + pre2022 done", flush=True)

    jobs = []
    for d0, d1, kind, svc, lyr, field in SOURCES:
        if kind not in kinds:
            continue
        d = date.fromisoformat(d0)
        while d <= date.fromisoformat(d1):
            jobs.append((d.isoformat(), kind, svc, lyr, field))
            d += timedelta(days=1)
    jobs.sort(key=lambda j: (j[1] != "control", j[0]))  # control layer first
    print(f"{len(jobs)} day-layer queries", flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for day, kind, n in ex.map(day_job, jobs):
            done += 1
            if done % 50 == 0 or n == 0:
                print(f"{done}/{len(jobs)} {kind} {day} n={n} {time.time() - t0:.0f}s", flush=True)
    print(f"all done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
