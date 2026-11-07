import asyncio
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from atproto import Client
from dotenv import load_dotenv
from post2schema import bluesky_post_to_schema

OUTPUT = Path("bluesky_ukraine_posts.out.jsonl")
INTERVAL = 300  # 5 minutes
MAX_SEEN = 10_000

QUERIES = {
    "war_general": '"Russia Ukraine" OR "Ukraine war"',
    "frontline":   "Bakhmut OR Avdiivka OR Kharkiv OR Zaporizhzhia OR Donetsk",
    "kyiv":        "(Kyiv OR Kiev) (missile OR drone OR strike)",
    "moscow":      "(Kremlin OR Putin) Ukraine",
    "ceasefire":   "(ceasefire OR \"peace talks\" OR negotiations) Ukraine",
}


def search(client: Client, query: str, limit: int = 50) -> list:
    res = client.app.bsky.feed.search_posts(
        params={"q": query, "limit": limit, "sort": "latest"}
    )
    print(len(res.posts))
    return res.posts


def save(query_name: str, post) -> None:
    record = bluesky_post_to_schema(post, query_name=query_name)
    with OUTPUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def poll_query(client: Client, name: str, query: str, seen: set, state: dict) -> None:
    backoff = 0
    while True:
        try:
            posts = await asyncio.to_thread(search, client, query)
            new = [p for p in posts if p.uri not in seen]
            for p in new:
                seen.add(p.uri)

            if not state["first_run"]:
                for p in reversed(new):  # oldest first
                    save(name, p)
                    print(f"[{name}] @{p.author.handle}: {p.record.text[:120]!r}")

            if len(seen) > MAX_SEEN:
                for uri in list(seen)[: len(seen) - MAX_SEEN]:
                    seen.discard(uri)

            backoff = 0

        except Exception as e:
            backoff = min(max(backoff * 2, INTERVAL), 900)
            print(f"[{name}] poll failed: {e}; backing off {backoff}s")
            await asyncio.sleep(backoff)
            continue

        await asyncio.sleep(INTERVAL + random.uniform(0, INTERVAL * 0.1))


async def main() -> None:
    client = Client()
    client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_APP_PASSWORD"])

    state = {"first_run": True}
    seen_sets = {name: set() for name in QUERIES}

    # Seed so startup doesn't flood the output with existing posts
    """for name, query in QUERIES.items():
        for p in await asyncio.to_thread(search, client, query):
            seen_sets[name].add(p.uri)"""
    state["first_run"] = False
    print(f"Seeded {len(QUERIES)} queries. Polling every {INTERVAL // 60} minutes.")

    await asyncio.gather(*(
        poll_query(client, name, query, seen_sets[name], state)
        for name, query in QUERIES.items()
    ))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())