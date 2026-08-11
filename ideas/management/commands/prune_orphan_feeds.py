"""Delete feeds linked to no idea (and their items).

Feed caps can leave a shared feed with no idea attached; those orphans keep
getting ingested forever. This prunes them.

    manage.py prune_orphan_feeds --dry-run   # report what would go
    manage.py prune_orphan_feeds             # delete them
"""

from django.core.management.base import BaseCommand

from ideas.models import Feed, FeedItem


class Command(BaseCommand):
    help = "Delete feeds that are linked to no idea (and their items)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the counts without deleting anything.",
        )

    def handle(self, *args, **options):
        orphans = Feed.objects.filter(idea_feeds__isnull=True)
        feed_count = orphans.count()
        item_count = FeedItem.objects.filter(feed__in=orphans).count()

        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {feed_count} orphan feeds and {item_count} items."
            )
            return

        orphans.delete()  # FeedItem cascades on the feed FK
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {feed_count} orphan feeds and {item_count} items."
            )
        )
