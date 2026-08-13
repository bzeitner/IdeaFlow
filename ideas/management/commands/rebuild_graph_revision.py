from django.core.management.base import BaseCommand

from ideas.models import GraphRevision


class Command(BaseCommand):
    help = "Advance the graph revision, invalidating any cached projection."

    def handle(self, *args, **options):
        marker, _ = GraphRevision.objects.get_or_create(pk=1)
        marker.revision += 1
        marker.save(update_fields=["revision", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Graph revision is now {marker.revision}."))
