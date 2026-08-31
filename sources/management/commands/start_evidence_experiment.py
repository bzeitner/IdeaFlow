import secrets

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sources.models import EvidenceExperiment


class Command(BaseCommand):
    help = "Start a bounded new-item evidence ranking experiment."

    def add_arguments(self, parser):
        parser.add_argument("key")
        parser.add_argument("--hypothesis", required=True)
        parser.add_argument("--primary-metric", default="useful")
        parser.add_argument("--treatment-percent", type=int, default=50)
        parser.add_argument("--minimum-sample-size", type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options["treatment_percent"] <= 99:
            raise CommandError("--treatment-percent must be between 1 and 99.")
        if options["minimum_sample_size"] < 1:
            raise CommandError("--minimum-sample-size must be positive.")
        if EvidenceExperiment.objects.filter(state=EvidenceExperiment.State.RUNNING).exists():
            raise CommandError("Only one evidence experiment may enroll at a time.")
        if EvidenceExperiment.objects.filter(key=options["key"]).exists():
            raise CommandError("An experiment with this key already exists; history is immutable.")
        experiment = EvidenceExperiment.objects.create(
            key=options["key"],
            hypothesis=options["hypothesis"],
            primary_metric=options["primary_metric"],
            treatment_percent=options["treatment_percent"],
            minimum_sample_size=options["minimum_sample_size"],
            salt=secrets.token_urlsafe(32),
            state=EvidenceExperiment.State.RUNNING,
            enrollment_started_at=timezone.now(),
            guardrails={
                "max_invalid_output_rate": 0.05,
                "max_exposure_imbalance": 0.15,
                "automatic_promotion": False,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"Started {experiment.key}; only items ingested at or after "
            f"{experiment.enrollment_started_at.isoformat()} are eligible."
        ))
