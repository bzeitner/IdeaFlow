from django.core.management.base import BaseCommand, CommandError

from ideas.graph.semantic import SemanticAPI, process_idea
from ideas.models import Idea, IdeaSemanticState, SemanticStatus


class Command(BaseCommand):
    help = "Embed changed idea research and propose semantic graph relationships."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Reprocess every idea.")
        parser.add_argument("--idea", type=int, help="Process one idea ID.")
        parser.add_argument("--limit", type=int, default=25, help="Maximum ideas per run.")

    def handle(self, *args, **options):
        if options["all"] and options["idea"]:
            raise CommandError("Use either --all or --idea, not both.")
        try:
            api = SemanticAPI()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        ideas = Idea.objects.prefetch_related("research_entries")
        if options["idea"]:
            ideas = ideas.filter(pk=options["idea"])
        elif not options["all"]:
            ideas = ideas.filter(semantic_state__status__in=[SemanticStatus.STALE, SemanticStatus.FAILED])
        processed = failed = 0
        for idea in ideas.order_by("updated_at")[: options["limit"]]:
            try:
                count = process_idea(idea, api=api)
                self.stdout.write(f"Idea {idea.pk}: generated {count} classified relationship(s)")
                processed += 1
            except Exception as exc:
                self.stderr.write(f"Idea {idea.pk}: {exc}")
                failed += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {processed}; failed {failed}."))
