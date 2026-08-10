"""Print ideas as JSON for a local agent to read.

    manage.py dump_idea 12        # one idea, full detail
    manage.py dump_idea           # all ideas, summary rows
    manage.py dump_idea --status current
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ideas.models import Idea
from ideas.serialize import idea_to_dict


class Command(BaseCommand):
    help = "Print one idea (with pk) or a list of ideas as JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "pk", nargs="?", type=int, help="Idea id. Omit to list all ideas."
        )
        parser.add_argument("--status", help="Filter the list by status.")
        parser.add_argument(
            "--indent", type=int, default=2, help="JSON indent (0 for compact)."
        )

    def handle(self, *args, **options):
        indent = options["indent"] or None
        if options["pk"] is not None:
            try:
                idea = (
                    Idea.objects.select_related("category", "stage", "parent")
                    .prefetch_related(
                        "resources",
                        "research_entries",
                        "research_entries__model",
                        "children",
                    )
                    .get(pk=options["pk"])
                )
            except Idea.DoesNotExist:
                raise CommandError(f"No idea with id {options['pk']}.")
            data = idea_to_dict(idea, detail=True)
        else:
            ideas = Idea.objects.select_related("category", "stage")
            if options["status"]:
                ideas = ideas.filter(status=options["status"])
            data = {"ideas": [idea_to_dict(i, detail=False) for i in ideas]}

        self.stdout.write(json.dumps(data, indent=indent, default=str))
