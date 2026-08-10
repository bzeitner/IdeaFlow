#!/usr/bin/env python3
"""Build one idea's scoring queue from the DEPLOYED app, as JSON.

The scoring agent (see ../score_items.sh) needs "the unsummarized items that
matter to idea N". The API narrows that to the idea's own feeds and hands back
each entry's stored body; the rest — the per-feed rating floor and the recency
window — is applied here. Needs no database access, just tools/ideaflow and its
bearer token.

    tools/score_queue.py 8 --min-rating 4 --since-days 30 --limit 25

Prints {"idea": ..., "queue_size": N, "items": [...]} where each item carries
the fields an agent needs to judge it: id, title, link, an excerpt of the
entry body, feed title and the idea's rating for that feed. The excerpt is
what keeps the agent from re-downloading a page the ingester already read.

Selection, in order:
  * items from feeds linked to the idea, with IdeaFeed rating >= --min-rating
  * published within --since-days (items with NO published_at are KEPT: a
    publisher emitting an unparseable date shouldn't fall out of the queue
    forever)
  * not yet summarized
  * newest first, capped at --limit
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

CLIENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ideaflow")

# Enough of the body to judge an item on; not so much that 25 of them swamp
# the agent's context.
CONTENT_EXCERPT_CHARS = 2_000


def client(*args):
    proc = subprocess.run(
        [sys.executable, CLIENT, *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.exit(f"error: `ideaflow {' '.join(args)}` failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def published(item):
    """Parse published_at, or None when absent/unparseable."""
    raw = item.get("published_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Build an idea's scoring queue.")
    p.add_argument("idea", type=int)
    p.add_argument("--min-rating", type=int, default=4, help="Min IdeaFeed rating.")
    p.add_argument("--since-days", type=int, default=30)
    p.add_argument("--limit", type=int, default=25, help="0 for no cap.")
    a = p.parse_args(argv)

    idea = client("dump-idea", str(a.idea))
    feeds = {
        f["id"]: f
        for f in idea.get("feeds", [])
        if (f.get("rating") or 0) >= a.min_rating
    }
    if not feeds:
        sys.exit(f"error: idea {a.idea} has no feeds rated >= {a.min_rating}.")

    items = client(
        "feed-items", "--unsummarized", "--content", "--idea", str(a.idea)
    )["items"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=a.since_days)

    queue = []
    for item in items:
        feed = feeds.get(item["feed_id"])
        if feed is None or item.get("summarized_at"):
            continue
        when = published(item)
        if when is not None and when < cutoff:
            continue
        queue.append(
            {
                "id": item["id"],
                "title": item["title"],
                "link": item["link"],
                "published_at": item.get("published_at"),
                "content": (item.get("content") or "")[:CONTENT_EXCERPT_CHARS],
                "feed_id": item["feed_id"],
                "feed_title": feed.get("title", ""),
                "feed_rating": feed.get("rating"),
            }
        )

    queue.sort(key=lambda i: i["published_at"] or "", reverse=True)
    total = len(queue)
    if a.limit:
        queue = queue[: a.limit]

    print(
        json.dumps(
            {
                "idea": {
                    "id": idea["id"],
                    "title": idea["title"],
                    "summary": idea.get("summary", ""),
                },
                "min_rating": a.min_rating,
                "since_days": a.since_days,
                "queue_size": total,
                "returned": len(queue),
                "items": queue,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
