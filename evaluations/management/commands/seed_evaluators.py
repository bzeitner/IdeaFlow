import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from evaluations.seeds import seed_evaluators


class Command(BaseCommand):
    help = "Idempotently seed proposed R5A v1 evaluators; never approves them or calls a provider."

    def handle(self, *args, **options):
        try:
            versions = seed_evaluators()
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps([{"id": v.pk, "key": v.evaluator.key,
                                     "version": v.version, "content_hash": v.content_hash,
                                     "decision_grade": False} for v in versions], indent=2))
