"""Write one neutral summary and an idea-specific usefulness assessment.

    manage.py summarize_feed_item 42 \
        --idea 7 --summary-file summary.md --model claude-haiku-4-5 --usefulness 4
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ideas.feeds import record_feed_item_summary
from ideas.models import FeedItem, Idea
from ideas.serialize import feed_item_to_dict


class Command(BaseCommand):
    help = "Record an agent summary + usefulness rating (1-5) on a feed item."

    def add_arguments(self, parser):
        parser.add_argument("pk", type=int, help="Feed item id.")
        parser.add_argument("--summary", default="", help="Summary text.")
        parser.add_argument(
            "--summary-file", help="Read the summary from this file (overrides --summary)."
        )
        parser.add_argument(
            "--model", default="other", help="AI model slug or name (default: other)."
        )
        parser.add_argument(
            "--usefulness", type=int, help="The agent's 1-5 usefulness rating."
        )
        parser.add_argument("--idea", type=int, help="Idea the usefulness applies to.")
        parser.add_argument("--relevance-note", default="")

    def handle(self, *args, **options):
        try:
            item = FeedItem.objects.get(pk=options["pk"])
        except FeedItem.DoesNotExist:
            raise CommandError(f"No feed item with id {options['pk']}.")

        summary = options["summary"]
        if options["summary_file"]:
            try:
                with open(options["summary_file"], encoding="utf-8") as fh:
                    summary = fh.read()
            except OSError as exc:
                raise CommandError(f"Could not read --summary-file: {exc}")

        idea = None
        if options["idea"] is not None:
            try:
                idea = Idea.objects.get(pk=options["idea"])
            except Idea.DoesNotExist:
                raise CommandError(f"No idea with id {options['idea']}.")
            if not idea.idea_feeds.filter(feed=item.feed).exists():
                raise CommandError("This feed item is not linked to that idea.")
        try:
            record_feed_item_summary(
                item,
                summary=summary,
                model=options["model"],
                idea=idea,
                usefulness=options["usefulness"],
                relevance_note=options["relevance_note"],
            )
        except (ValueError, LookupError) as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            json.dumps(feed_item_to_dict(item, idea=idea), indent=2, default=str)
        )
        self.stderr.write(self.style.SUCCESS(f"Summarized feed item {item.pk}."))
