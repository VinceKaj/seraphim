"""Fetch ISW "Russian Offensive Campaign Assessment" reports from the Wayback Machine.

understandingwar.org is Cloudflare-blocked from this VM (HTTP 403), so we read Wayback captures (id_ = raw page).
Output: data/isw/reports/<YYYY-MM-DD>.html (raw HTML, decompressed) and <YYYY-MM-DD>.txt (plain text),
        data/isw/reports/index.tsv (date, wayback timestamp, original url, status).
Rate: 1 request / 4 s, back-off on 429/5xx. Re-runs skip dates that already have a .html file.
Does NOT write to the record store.
Usage: uv run trust/fetch_isw_text.py [--start 2023-01-01] [--end 2026-11-07]
"""
import argparse
import gzip
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "isw" / "reports"
CDX = "https://web.archive.org/cdx/search/cdx"
PREFIXES = [
    "understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment",
    "understandingwar.org/backgrounder/russian-offensive-campaign-assessment",
]
MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
SLUG = re.compile(r"campaign-assessment-+([a-z]+)-(\d{1,2})(?:-(\d{4}))?(?=[/?#-]|$)", re.I)
DELAY = 4.0
UA = "seraphim-research/0.1 (hackathon; contact via repo owner)"


def http(url: str, tries: int = 6) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=120) as r:
                b = r.read()
            return gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            wait = 60 * (i + 1) if e.code == 429 else 10 * (i + 1)
            print(f"HTTP {e.code}, wait {wait}s: {url[:100]}", file=sys.stderr, flush=True)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            print(f"{e}, wait {10 * (i + 1)}s: {url[:100]}", file=sys.stderr, flush=True)
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"giving up: {url}")


def slug_date(url: str):
    m = SLUG.search(url.lower())
    if not m or m.group(1) not in MONTHS:
        return None
    y = int(m.group(3)) if m.group(3) else 2022
    try:
        return date(y, MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def captures() -> dict:
    """date -> list of (timestamp, original url), best first."""
    by_day = {}
    for pre in PREFIXES:
        q = urllib.parse.urlencode({"url": pre + "*", "output": "json", "fl": "timestamp,original",
                                    "filter": "statuscode:200"})
        rows = json.loads(http(f"{CDX}?{q}"))[1:]
        time.sleep(DELAY)
        for ts, orig in rows:
            if "__cf_chl" in orig:
                continue
            d = slug_date(orig)
            if d:
                by_day.setdefault(d, []).append((ts, orig))
    for d, caps in by_day.items():
        # prefer clean URLs (no query), then the earliest capture on/after the report date
        caps.sort(key=lambda c: ("?" in c[1], c[0] < d.strftime("%Y%m%d"), c[0]))
    return by_day


def to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|svg|noscript|nav|header|footer)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<(p|li|h\d|br|div|tr)\b[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip() + "\n"


def good(page: str) -> bool:
    t = re.search(r"<title>([^<]*)", page)
    return bool(t) and "Campaign Assessment" in t.group(1) and "Cloudflare" not in t.group(1) \
        and "Just a moment" not in t.group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-11-07")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    caps = captures()
    days = sorted(d for d in caps if start <= d <= end)
    total = (end - start).days + 1
    print(f"{len(days)} of {total} days have Wayback captures; ETA ~{len(days) * (DELAY + 2) / 60:.0f} min",
          flush=True)
    idx = open(OUT / "index.tsv", "a")
    t0 = time.time()
    for n, d in enumerate(days, 1):
        if (OUT / f"{d}.html").exists():
            continue
        status = "fail"
        for ts, orig in caps[d][:3]:
            try:
                page = http(f"https://web.archive.org/web/{ts}id_/{orig}").decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                print(f"{d} {ts} error {e}", file=sys.stderr, flush=True)
                time.sleep(DELAY)
                continue
            time.sleep(DELAY)
            if not good(page):
                continue
            (OUT / f"{d}.html").write_text(page)
            (OUT / f"{d}.txt").write_text(to_text(page))
            status = "ok"
            break
        idx.write(f"{d}\t{ts}\t{orig}\t{status}\n")
        idx.flush()
        if n % 20 == 0 or status != "ok":
            el = time.time() - t0
            print(f"{n}/{len(days)} {d} {status} {el / 60:.0f} min elapsed", flush=True)
    missing = sorted({start.fromordinal(o) for o in range(start.toordinal(), end.toordinal() + 1)} - set(days))
    print(f"done. days with no capture: {len(missing)} e.g. {[str(m) for m in missing[:10]]}", flush=True)


if __name__ == "__main__":
    main()
