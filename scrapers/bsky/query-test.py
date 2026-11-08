import os
import sys

from atproto import Client
from dotenv import load_dotenv

load_dotenv()

query = sys.argv[1] if len(sys.argv) > 1 else '"Ukraine war"'
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 25

client = Client()
client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_APP_PASSWORD"])

res = client.app.bsky.feed.search_posts(
    params={"q": query, "limit": limit, "sort": "latest"}
)

print(f"Query: {query}")
print(f"Results: {len(res.posts)}\n")

for p in res.posts:
    print(f"{p.record.created_at}  @{p.author.handle}")
    print(f"  {p.record.text[:150]!r}\n")