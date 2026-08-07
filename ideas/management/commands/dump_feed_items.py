"""Print feed items as JSON for the ingesting agent.

    manage.py dump_feed_items --unsummarized   # the work queue for the agent
    manage.py dump_feed_items --feed 3
    manage.py dump_feed_items 42                # one item (with pk)
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ideas.models import FeedItem
from ideas.serialize import feed_item_to_dict


class Command(BaseCommand):
    help = "Print feed items as JSON (optionally only unsummarized ones)."

    def add_arguments(self, parser):
        parser.add_argument("pk", nargs="?", type=int, help="A single item id.")
        parser.add_argument("--feed", type=int, help="Filter by feed id.")
        parser.add_argument(
            "--unsummarized",
            action="store_true",
            help="Only items with no summary yet (the agent's work queue).",
        )
        parser.add_argument("--indent", type=int, default=2)

    def handle(self, *args, **options):
        indent = options["indent"] or None
        if options["pk"] is not None:
            try:
                item = FeedItem.objects.select_related("summary_model").get(
                    pk=options["pk"]
                )
            except FeedItem.DoesNotExist:
                raise CommandError(f"No feed item with id {options['pk']}.")
            self.stdout.write(json.dumps(feed_item_to_dict(item), indent=indent, default=str))
            return

        items = FeedItem.objects.select_related("summary_model")
        if options["feed"]:
            items = items.filter(feed_id=options["feed"])
        if options["unsummarized"]:
            items = items.filter(summarized_at__isnull=True)
        data = {"items": [feed_item_to_dict(i) for i in items]}
        self.stdout.write(json.dumps(data, indent=indent, default=str))
