"""Register a feed (or attach an existing one to an idea).

    manage.py add_feed --url https://example.com/feed.xml --title "Example"
    manage.py add_feed --url https://example.com/feed.xml --idea 12
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ideas.feeds import is_acceptable_feed_url
from ideas.models import Feed, Idea
from ideas.serialize import feed_to_dict


class Command(BaseCommand):
    help = "Create a feed (idempotent by URL) and optionally link it to an idea."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="Feed URL (unique).")
        parser.add_argument("--title", default="", help="Optional title.")
        parser.add_argument(
            "--idea", type=int, help="Idea id to associate this feed with."
        )

    def handle(self, *args, **options):
        if not is_acceptable_feed_url(options["url"]):
            raise CommandError(
                "Feed URL must be http(s) and must not point at a private address."
            )
        feed, created = Feed.objects.get_or_create(
            url=options["url"], defaults={"title": options["title"]}
        )
        if options["title"] and not feed.title:
            feed.title = options["title"]
            feed.save(update_fields=["title"])

        if options["idea"]:
            try:
                idea = Idea.objects.get(pk=options["idea"])
            except Idea.DoesNotExist:
                raise CommandError(f"No idea with id {options['idea']}.")
            feed.ideas.add(idea)

        self.stdout.write(json.dumps(feed_to_dict(feed), indent=2, default=str))
        self.stderr.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Reused'} feed {feed.pk}."
            )
        )
