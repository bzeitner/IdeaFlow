from django.core.management.base import BaseCommand, CommandError

from ideas.models import IdeaRelation, RelationType


class Command(BaseCommand):
    help = "Check graph invariants and report invalid dependency cycles."

    def handle(self, *args, **options):
        problems = []
        for relation in IdeaRelation.objects.filter(relation_type=RelationType.DEPENDS_ON):
            if relation._creates_dependency_cycle():
                problems.append(f"dependency cycle involving relation {relation.pk}")
        if problems:
            raise CommandError("; ".join(problems))
        self.stdout.write(self.style.SUCCESS("Graph audit passed."))
