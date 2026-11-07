import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from twscrape import API

# Free proxies, use if we get more accounts in the db
# PROXIES = ["159.203.61.169:3128", "138.68.60.8:3128", "165.154.162.73:8888", "134.209.29.120:3128", "103.237.102.191:11111", "161.35.70.249:80", "139.162.78.109:8080", "176.111.37.5:39811", "130.110.103.245:3128", "176.111.37.216:39811", "128.199.202.122:8080", "139.59.1.14:80", "102.244.78.61:8086", "102.244.78.61:8082", "102.244.78.61:8085", "102.244.78.61:8090", "102.244.78.61:8081", "102.244.78.61:8084", "122.102.52.24:1083", "177.234.217.85:999", "181.78.195.136:999"]

OUTPUT = Path("ukraine_war_tweets.out.jsonl")
INTERVAL = 300  # 5 minutes

QUERIES = {
    "war_general": '("Russia Ukraine" OR "Ukraine war") -filter:retweets',
    "frontline":   '(Bakhmut OR Avdiivka OR Kharkiv OR Zaporizhzhia OR Donetsk) -filter:retweets',
    "kyiv":        '(Kyiv OR Kiev) (missile OR drone OR strike) -filter:retweets',
    "moscow":      '(Kremlin OR Putin) Ukraine -filter:retweets',
    "ceasefire":   '(ceasefire OR "peace talks" OR negotiations) Ukraine -filter:retweets',
}

MAX_SEEN = 10_000  # cap memory use for the long-running process


def save(query_name: str, tweet) -> None:
    record = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "query": query_name,
        "id": tweet.id,
        "date": tweet.date.isoformat(),
        "username": tweet.user.username,
        "text": tweet.rawContent,
        "url": tweet.url,
    }
    with OUTPUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def poll_query(api: API, name: str, query: str, seen: set, state: dict) -> None:
    backoff = 0
    while True:
        try:
            batch = []
            async for tweet in api.search(query, limit=50):
                if tweet.id not in seen:
                    seen.add(tweet.id)
                    batch.append(tweet)

            if state["first_run"]:
                # Record what's already there without saving it
                pass
            else:
                for tweet in reversed(batch):  # oldest first
                    save(name, tweet)
                    print(f"[{name}] @{tweet.user.username}: {tweet.rawContent[:120]!r}")

            # Trim the seen set so memory doesn't grow forever
            if len(seen) > MAX_SEEN:
                for old in sorted(seen)[: len(seen) - MAX_SEEN]:
                    seen.discard(old)

            backoff = 0

        except Exception as e:
            backoff = min(max(backoff * 2, INTERVAL), 900)
            print(f"[{name}] poll failed: {e}; backing off {backoff}s")
            await asyncio.sleep(backoff)
            continue

        await asyncio.sleep(INTERVAL + random.uniform(0, INTERVAL * 0.1))


async def main() -> None:
    api = API()  # uses accounts already added to accounts.db
    state = {"first_run": True}
    seen_sets = {name: set() for name in QUERIES}

    # Seed each query once so startup doesn't flood the output
    for name, query in QUERIES.items():
        async for tweet in api.search(query, limit=50):
            seen_sets[name].add(tweet.id)
    state["first_run"] = False
    print(f"Seeded {len(QUERIES)} queries. Polling every {INTERVAL // 60} minutes.")

    tasks = [
        poll_query(api, name, query, seen_sets[name], state)
        for name, query in QUERIES.items()
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())