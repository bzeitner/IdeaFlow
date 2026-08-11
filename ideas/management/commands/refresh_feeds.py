"""Download active feeds (conditional GET) and ingest new entries, once each.

    manage.py refresh_feeds            # all active feeds
    manage.py refresh_feeds --feed 3   # just one

New entries are stored unsummarized; run dump_feed_items --unsummarized to see
what an agent still needs to summarize.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ideas.feeds import fetch_and_ingest
from ideas.models import Feed


class Command(BaseCommand):
    help = "Fetch active feeds and ingest their new entries (deduped by guid)."

    def add_arguments(self, parser):
        parser.add_argument("--feed", type=int, help="Only refresh this feed id.")

    def handle(self, *args, **options):
        # Only fetch feeds still linked to an idea — never re-ingest orphans.
        feeds = Feed.objects.filter(is_active=True, idea_feeds__isnull=False).distinct()
        if options["feed"]:
            feeds = feeds.filter(pk=options["feed"])
            if not feeds.exists():
                raise CommandError(f"No active feed with id {options['feed']}.")

        report = []
        for feed in feeds:
            try:
                status, new_items = fetch_and_ingest(feed)
            except Exception as exc:  # network/parse errors shouldn't stop the batch
                self.stderr.write(self.style.ERROR(f"feed {feed.pk} ({feed.url}): {exc}"))
                report.append({"feed": feed.pk, "error": str(exc)})
                continue
            report.append(
                {
                    "feed": feed.pk,
                    "title": feed.title,
                    "http_status": status,
                    "new_items": len(new_items),
                }
            )
            self.stderr.write(
                self.style.SUCCESS(
                    f"feed {feed.pk}: {len(new_items)} new (status {status})."
                )
            )

        self.stdout.write(json.dumps(report, indent=2, default=str))
