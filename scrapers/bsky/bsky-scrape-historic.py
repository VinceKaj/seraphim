import asyncio
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from atproto import Client
from dotenv import load_dotenv
from post2schema import bluesky_post_to_schema

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "record-store"))
import store_client as sc  # noqa: E402

INTERVAL = 60            # check for more history every minute
PAGE_SIZE = 100
MAX_PAGES_PER_TICK = 3   # backward pages per query per tick, to stay gentle on limits
MAX_SEEN = 10_000

QUERIES = {
    "war_general": '"Russia Ukraine" OR "Ukraine war"',
    "frontline":   "Bakhmut OR Avdiivka OR Kharkiv OR Zaporizhzhia OR Donetsk",
    "kyiv":        "(Kyiv OR Kiev) (missile OR drone OR strike)",
    "moscow":      "(Kremlin OR Putin) Ukraine",
    "ceasefire":   "(ceasefire OR \"peace talks\" OR negotiations) Ukraine",
}


@dataclass
class Cursor:
    oldest: datetime | None = None   # oldest created_at seen; the next `until` cutoff
    exhausted: bool = False          # True once the crawl hits the end of results


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _created(post) -> datetime:
    return _parse_iso(post.record.created_at)


def search(client: Client, query: str, until: datetime | None) -> list:
    params = {"q": query, "limit": PAGE_SIZE, "sort": "latest"}
    if until is not None:
        params["until"] = _iso(until)
    return client.app.bsky.feed.search_posts(params=params).posts


def save(query_name: str, post) -> None:
    record = bluesky_post_to_schema(post, query_name=query_name)
    rkey = post.uri.split("/")[-1]
    sc.create("post", f"bsky:{post.author.did}", rkey, record)


async def store_page(name: str, posts: list, seen: set, cur: Cursor) -> None:
    # Save oldest first. Mark seen only after a successful write so failures retry.
    for p in sorted(posts, key=_created):
        if p.uri in seen:
            continue
        try:
            await asyncio.to_thread(save, name, p)
            seen.add(p.uri)
            print(f"[{name}] @{p.author.handle}: {p.record.text[:120]!r}")
        except Exception as e:
            print(f"[{name}] store write failed for {p.uri}: {e}")


async def backward_crawl(client, name, query, seen, cur: Cursor) -> None:
    if cur.exhausted:
        return
    for _ in range(MAX_PAGES_PER_TICK):
        posts = await asyncio.to_thread(search, client, query, cur.oldest)
        if not posts:
            cur.exhausted = True
            print(f"[{name}] crawl reached the end of available results")
            return

        page_oldest = min(_created(p) for p in posts)
        # No progress means every post on this page is at or after the cutoff, so stop.
        if cur.oldest is not None and page_oldest >= cur.oldest:
            cur.exhausted = True
            print(f"[{name}] crawl stalled, no older posts returned")
            return

        await store_page(name, posts, seen, cur)
        cur.oldest = page_oldest


async def poll_query(client: Client, name: str, query: str, seen: set, cur: Cursor) -> None:
    backoff = 0
    while not cur.exhausted:
        try:
            await backward_crawl(client, name, query, seen, cur)

            if len(seen) > MAX_SEEN:
                for uri in list(seen)[: len(seen) - MAX_SEEN]:
                    seen.discard(uri)

            backoff = 0

        except Exception as e:
            backoff = min(max(backoff * 2, INTERVAL), 900)
            print(f"[{name}] crawl failed: {e}; backing off {backoff}s")
            await asyncio.sleep(backoff)
            continue

        await asyncio.sleep(INTERVAL + random.uniform(0, INTERVAL * 0.1))
    print(f"[{name}] finished, no further history")


async def main() -> None:
    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_APP_PASSWORD"])

    print(f"Backward crawl of {len(QUERIES)} queries, {MAX_PAGES_PER_TICK} pages per tick.")
    await asyncio.gather(*(
        poll_query(client, name, query, set(), Cursor())
        for name, query in QUERIES.items()
    ))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())