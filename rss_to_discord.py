"""Post new BleepingComputer articles to a Discord channel through a webhook.

Runs on a schedule via GitHub Actions. Remembers which articles it has already
posted in seen.json, which the workflow commits back to the repository.
"""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://www.bleepingcomputer.com/feed/"
STATE_FILE = "seen.json"
MAX_REMEMBERED = 300  # how many article IDs to keep in seen.json
USER_AGENT = "Mozilla/5.0 (compatible; rss-to-discord/1.0)"

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def fetch_items():
    """Return feed items as dicts, newest first (the order the feed lists them)."""
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"Could not fetch the feed: HTTP {e.code}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the feed: {e.reason}")

    items = []
    for item in ET.fromstring(data).iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        if title and link:
            items.append({"id": guid, "title": title, "link": link})
    return items


def load_seen():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None  # first run


def save_seen(ids):
    with open(STATE_FILE, "w") as f:
        json.dump(ids[:MAX_REMEMBERED], f, indent=2)


def post_to_discord(item):
    payload = {
        "content": f"**{item['title']}**\n{item['link']}",
        "allowed_mentions": {"parse": []},  # never ping @everyone etc.
    }
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    for _ in range(5):
        try:
            urllib.request.urlopen(req, timeout=30)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited: wait as long as Discord asks
                retry_after = json.loads(e.read() or b"{}").get("retry_after", 2)
                time.sleep(float(retry_after) + 0.5)
                continue
            sys.exit(f"Discord rejected the message: HTTP {e.code} {e.read()[:200]!r}")
    sys.exit("Gave up after being rate-limited by Discord repeatedly")


def main():
    if not WEBHOOK_URL:
        sys.exit("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.")

    items = fetch_items()
    seen = load_seen()

    if seen is None:
        # First run: post only the latest article so the channel isn't flooded.
        to_post = items[:1]
        seen = [i["id"] for i in items[1:]]
    else:
        seen_ids = set(seen)
        to_post = [i for i in items if i["id"] not in seen_ids]

    print(f"{len(items)} items in feed, {len(to_post)} new")

    try:
        for item in reversed(to_post):  # oldest first, so the channel reads in order
            post_to_discord(item)
            seen.insert(0, item["id"])
            print(f"Posted: {item['title']}")
            time.sleep(1)
    finally:
        save_seen(seen)  # keep progress even if a post fails partway


if __name__ == "__main__":
    main()
