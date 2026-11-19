"""Post new BleepingComputer articles to a Discord channel through a webhook.

Runs on a schedule via GitHub Actions. Remembers which articles it has already
posted in seen.json, which the workflow commits back to the repository.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

# ---------------- Settings you can change ----------------

FEED_URL = "https://www.bleepingcomputer.com/feed/"

# Colour of the embed's side bar, picked by the article's first matching category.
CATEGORY_COLORS = {
    "security": 0xE74C3C,                 # red
    "microsoft": 0x0078D4,                # blue
    "artificial intelligence": 0xE67E22,  # orange
    "technology": 0x1ABC9C,               # teal
    "software": 0x9B59B6,                 # purple
    "google": 0x34A853,                   # green
    "apple": 0xA2AAAD,                    # grey
    "linux": 0xF1C40F,                    # yellow
}
DEFAULT_COLOR = 0x5865F2  # Discord blurple

SUMMARY_LENGTH = 300  # how many characters of the summary to show

# ---------------- End of settings ----------------

STATE_FILE = "seen.json"
MAX_REMEMBERED = 300
USER_AGENT = "Mozilla/5.0 (compatible; rss-to-discord/1.0)"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
DC = "{http://purl.org/dc/elements/1.1/}"


def clean_summary(raw):
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw or "")).split())
    if len(text) > SUMMARY_LENGTH:
        text = text[:SUMMARY_LENGTH].rsplit(" ", 1)[0] + "…"
    return text


def fetch_items():
    """Return feed items as dicts, newest first (the order the feed lists them)."""
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            root = ET.fromstring(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Could not fetch the feed: HTTP {e.code}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the feed: {e.reason}")
    except ET.ParseError:
        sys.exit("The feed didn't come back as valid RSS (the site may be blocking requests)")

    items = []
    for el in root.iter("item"):
        title = html.unescape((el.findtext("title") or "").strip())
        link = (el.findtext("link") or "").strip()
        if not (title and link):
            continue
        items.append({
            "id": (el.findtext("guid") or link).strip(),
            "title": title,
            "link": link,
            "categories": [c.text.strip() for c in el.findall("category") if c.text and c.text.strip()],
            "author": (el.findtext(DC + "creator") or "").strip(),
            "summary": clean_summary(el.findtext("description")),
            "published": (el.findtext("pubDate") or "").strip(),
        })
    return items


def build_embed(item):
    color = DEFAULT_COLOR
    for cat in item["categories"]:
        if cat.lower() in CATEGORY_COLORS:
            color = CATEGORY_COLORS[cat.lower()]
            break

    # Footer: "Security, Microsoft  |  Author Name", followed by Discord's own timestamp
    footer = "  |  ".join(p for p in [", ".join(item["categories"]), item["author"]] if p)

    embed = {"title": item["title"][:256], "url": item["link"], "color": color}
    if item["summary"]:
        embed["description"] = item["summary"]
    if footer:
        embed["footer"] = {"text": footer[:2048]}
    try:
        embed["timestamp"] = parsedate_to_datetime(item["published"]).isoformat()
    except (TypeError, ValueError):
        pass
    return embed


def post_to_discord(item):
    payload = {"embeds": [build_embed(item)], "allowed_mentions": {"parse": []}}
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
            sys.exit(f"Discord rejected the message: HTTP {e.code} {e.read()[:300]!r}")
    sys.exit("Gave up after being rate-limited by Discord repeatedly")


def load_seen():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None  # first run


def save_seen(ids):
    with open(STATE_FILE, "w") as f:
        json.dump(ids[:MAX_REMEMBERED], f, indent=2)


def main():
    if not WEBHOOK_URL:
        sys.exit("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.")

    items = fetch_items()
    seen = load_seen()

    if seen is None:
        # First run: post only the latest article so the channel isn't flooded.
        new = items[:1]
        seen = [i["id"] for i in items[1:]]
    else:
        seen_ids = set(seen)
        new = [i for i in items if i["id"] not in seen_ids]

    print(f"{len(items)} items in feed, {len(new)} new")

    try:
        for item in reversed(new):  # oldest first, so the channel reads in order
            post_to_discord(item)
            seen.insert(0, item["id"])
            print(f"Posted: {item['title']}")
            time.sleep(1)
    finally:
        save_seen(seen)  # keep progress even if a post fails partway


if __name__ == "__main__":
    main()
