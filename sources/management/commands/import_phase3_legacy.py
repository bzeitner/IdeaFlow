import json

from django.core.management.base import BaseCommand

from ideas.models import (
    Feed, FeedItem, FeedItemAssessment, IdeaFeed, IdeaRelation,
    IdeaRelationSuggestion, RelationshipCouncilReview, RepeatResult,
)
from sources.importers import import_legacy_phase3


class Command(BaseCommand):
    help = "Idempotently import Phase 3 source, graph, feedback, and repeat history."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["dry_run"]:
            counts = {
                "sources": Feed.objects.count(),
                "subscriptions": IdeaFeed.objects.count(),
                "items": FeedItem.objects.count(),
                "feedback": FeedItemAssessment.objects.count(),
                "relations": IdeaRelation.objects.count(),
                "suggestions": IdeaRelationSuggestion.objects.count(),
                "reviews": RelationshipCouncilReview.objects.count(),
                "repeat_results": RepeatResult.all_objects.count(),
            }
        else:
            counts = import_legacy_phase3()
        self.stdout.write(json.dumps({"dry_run": options["dry_run"], "counts": counts}, sort_keys=True))
