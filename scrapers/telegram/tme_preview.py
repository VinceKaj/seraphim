"""Scrape public Telegram channels through the web preview (https://t.me/s/<channel>), no account needed.

Writes `post` records with the same ids as Huge's Telethon dumps (source=telegram:<channel>, source_id=<msg id>),
so both importers are idempotent together. Unlike the dumps, forwards carry the original channel username, so
we write proper `forward` links (needed for copy detection in the trust layer).

Pages backward with ?before=<id> until posts are older than --days (or --since).
Usage: uv run scrapers/telegram/tme_preview.py --days 45 GeneralStaffZSU wargonzo ...
       uv run scrapers/telegram/tme_preview.py --days 45 --list scrapers/telegram/channels_isw.txt
"""
import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "record-store"))
import store_client as sc  # noqa: E402

UA = "Mozilla/5.0 (X11; Linux x86_64) seraphim-research"
DELAY = 1.0
S = requests.Session()
S.headers["User-Agent"] = UA


def text_of(el):
    if el is None:
        return ""
    for br in el.find_all("br"):
        br.replace_with("\n")
    return el.get_text("")


def parse(html, ch):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for m in soup.select("div.tgme_widget_message[data-post]"):
        post = m["data-post"]
        c, _, mid = post.partition("/")
        if not mid.isdigit():
            continue
        t = m.select_one("time[datetime]")
        if t is None:
            continue
        links = []
        fw = m.select_one("a.tgme_widget_message_forwarded_from_name[href]")
        if fw:
            mm = re.match(r"https://t\.me/([A-Za-z0-9_]+)/(\d+)", fw["href"])
            if mm:
                links.append({"type": "forward", "target": sc.key("post", f"telegram:{mm[1]}", mm[2])})
        rp = m.select_one("a.tgme_widget_message_reply[href]")
        if rp:
            mm = re.match(r"https://t\.me/([A-Za-z0-9_]+)/(\d+)", rp["href"])
            if mm:
                links.append({"type": "reply", "target": sc.key("post", f"telegram:{mm[1]}", mm[2])})
        body = m.select_one(".tgme_widget_message_text")
        for a in (body.select("a[href]") if body else []):
            h = a["href"]
            if h.startswith("http") and "t.me/" not in h:
                links.append({"type": "url", "target": h})
        views = m.select_one(".tgme_widget_message_views")
        media = []
        for ph in m.select("a.tgme_widget_message_photo_wrap[href]"):
            media.append({"type": "photo", "ref": ph["href"]})
        for v in m.select("a.tgme_widget_message_video_player[href]"):
            media.append({"type": "video", "ref": v["href"]})
        out.append(dict(channel=c, id=int(mid), posted_at=t["datetime"], text=text_of(body), links=links,
                        media=media, views=views.get_text() if views else None, html=str(m)[:20000]))
    more = soup.select_one("a.tme_messages_more[data-before]")
    return out, (int(more["data-before"]) if more else None)


def views_int(v):
    if not v:
        return None
    v = v.strip().upper()
    mult = 1_000_000 if v.endswith("M") else 1000 if v.endswith("K") else 1
    try:
        return int(float(v.rstrip("MK")) * mult)
    except ValueError:
        return None


def scrape(ch, since, lang):
    before, new = None, 0
    seen = 0
    while True:
        url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
        try:
            r = S.get(url, timeout=30)
        except requests.ConnectionError:
            time.sleep(10)
            continue
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        msgs, nxt = parse(r.text, ch)
        if not msgs:
            break
        now = datetime.now(timezone.utc).isoformat()
        oldest = None
        for m in msgs:
            pa = datetime.fromisoformat(m["posted_at"])
            oldest = pa if oldest is None or pa < oldest else oldest
            if pa < since:
                continue
            data = {"url": f"https://t.me/{m['channel']}/{m['id']}", "author": f"telegram:{m['channel']}",
                    "posted_at": m["posted_at"], "scraped_at": now, "lang": lang, "text": m["text"],
                    "views": views_int(m["views"]), "links": m["links"], "media": m["media"],
                    "raw": {"via": "tme_preview", "data_post": f"{m['channel']}/{m['id']}", "html": m["html"]}}
            new += sc.create("post", f"telegram:{m['channel']}", str(m["id"]), data)["new"]
            seen += 1
        if oldest < since or nxt is None or nxt == before:
            break
        before = nxt
        time.sleep(DELAY)
    return seen, new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channels", nargs="*")
    ap.add_argument("--list")
    ap.add_argument("--days", type=int, default=45)
    a = ap.parse_args()
    chans = list(a.channels)
    if a.list:
        for line in open(a.list):
            line = line.split("#")[0].strip()
            if line:
                chans.append(line)
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    for item in chans:
        ch, _, lang = item.partition(":")
        try:
            seen, new = scrape(ch, since, lang or None)
            print(f"{ch}: {seen} posts in window, {new} new", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{ch}: ERROR {e}", flush=True)


if __name__ == "__main__":
    main()
