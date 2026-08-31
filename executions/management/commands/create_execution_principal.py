import secrets

from django.core.management.base import BaseCommand, CommandError

from executions.models import ServicePrincipal


DEFAULT_SCOPES = ["execution:write", "execution:read"]


class Command(BaseCommand):
    help = "Create a scoped execution service principal and print its token once."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--scope", action="append", dest="scopes")

    def handle(self, *args, **options):
        name = options["name"].strip()
        if not name:
            raise CommandError("name is required")
        if ServicePrincipal.objects.filter(name=name).exists():
            raise CommandError(f"Service principal already exists: {name}")
        token = secrets.token_urlsafe(48)
        principal = ServicePrincipal.objects.create(
            name=name,
            token_hash=ServicePrincipal.hash_token(token),
            scopes=options["scopes"] or DEFAULT_SCOPES,
        )
        self.stdout.write(
            f"Created {principal.name}. Store this token now; it cannot be recovered:\n{token}"
        )
