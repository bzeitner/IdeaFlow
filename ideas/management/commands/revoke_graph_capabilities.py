from django.core.management.base import BaseCommand
from django.utils import timezone

from ideas.models import GraphAccessCapability


class Command(BaseCommand):
    help = "Revoke active Graph Lab read capabilities."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Also mark expired capabilities revoked.")

    def handle(self, *args, **options):
        capabilities = GraphAccessCapability.objects.filter(revoked_at__isnull=True)
        if not options["all"]:
            capabilities = capabilities.filter(expires_at__gt=timezone.now())
        count = capabilities.update(revoked_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"Revoked {count} graph capability/capabilities."))
