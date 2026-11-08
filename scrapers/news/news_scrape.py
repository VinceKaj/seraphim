import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
import trafilatura

USER_AGENT = ""
DELAY_PER_DOMAIN = 3.0   # seconds between requests to the same site
TIMEOUT = 20
CACHE = Path("fetch_cache.jsonl")

_last_hit: dict[str, float] = {}
_robots: dict[str, RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    parts = urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
            _robots[base] = rp
        except Exception:
            _robots[base] = None  # robots.txt unreachable; see notes
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def throttle(domain: str) -> None:
    wait = _last_hit.get(domain, 0) + DELAY_PER_DOMAIN - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_hit[domain] = time.time()


def fetch_text(url: str) -> tuple[str | None, str]:
    if not robots_allows(url):
        return None, "blocked_by_robots"
    throttle(urlparse(url).netloc)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, f"error: {e}"
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    text = trafilatura.extract(r.text, include_comments=False, include_tables=False)
    if not text:
        return None, "no_text_extracted"
    return text, "ok"


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    with CACHE.open(encoding="utf-8") as f:
        return {entry["url"]: entry for entry in map(json.loads, f)}


def enrich(in_path: Path, out_path: Path) -> None:
    cache = load_cache()
    ok = failed = cached = 0

    with CACHE.open("a", encoding="utf-8") as cache_file, \
         in_path.open(encoding="utf-8") as src, \
         out_path.open("w", encoding="utf-8") as dst:

        for line in src:
            rec = json.loads(line)
            url = rec.get("url")

            if url and url in cache:
                entry = cache[url]
                cached += 1
            elif url:
                text, status = fetch_text(url)
                entry = {"url": url, "text": text, "status": status}
                cache[url] = entry
                cache_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cache_file.flush()
                print(f"{status:>20}  {url}")
            else:
                entry = {"text": None, "status": "no_url"}

            if entry.get("text"):
                rec["text"] = entry["text"]  # full article body replaces the CSV summary
                ok += 1
            else:
                failed += 1  # keep the original description

            dst.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Enriched {ok}, kept description for {failed} (cached hits: {cached}).")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python news_scrape.py articles.jsonl articles_full.jsonl")
    enrich(Path(sys.argv[1]), Path(sys.argv[2]))