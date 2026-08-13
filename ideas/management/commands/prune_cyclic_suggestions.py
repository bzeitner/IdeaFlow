from django.core.management.base import BaseCommand

from ideas.graph.semantic import supersede_cyclic_suggestions


class Command(BaseCommand):
    help = "Supersede pending semantic dependency suggestions that would create cycles."

    def handle(self, *args, **options):
        count = supersede_cyclic_suggestions()
        self.stdout.write(self.style.SUCCESS(f"Superseded {count} cyclic suggestion(s)."))
