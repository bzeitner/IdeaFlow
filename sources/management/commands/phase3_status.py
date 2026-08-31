import json

from django.core.management.base import BaseCommand
from django.db.models import Avg, Count

from sources.models import (
    EvidenceAssignment, EvidenceCandidate, EvidenceExperiment,
    EvidenceObservation, Source, SourceItem, Subscription,
)


class Command(BaseCommand):
    help = "Report Phase 3 import, bounded-backlog, exposure, and experiment status."

    def handle(self, *args, **options):
        experiments = []
        for experiment in EvidenceExperiment.objects.all().order_by("created_at"):
            variants = list(
                EvidenceAssignment.objects.filter(experiment=experiment)
                .values("variant")
                .annotate(assignments=Count("id"))
                .order_by("variant")
            )
            observations = list(
                EvidenceObservation.objects.filter(assignment__experiment=experiment)
                .values("assignment__variant", "metric")
                .annotate(n=Count("id"), mean=Avg("value"))
                .order_by("assignment__variant", "metric")
            )
            experiments.append({
                "key": experiment.key,
                "state": experiment.state,
                "enrollment_started_at": experiment.enrollment_started_at,
                "variants": variants,
                "observations": observations,
            })
        report = {
            "sources": Source.objects.count(),
            "subscriptions": Subscription.objects.count(),
            "source_items": {
                "historical_ineligible": SourceItem.objects.filter(eligible_for_processing=False).count(),
                "new_eligible": SourceItem.objects.filter(eligible_for_processing=True).count(),
            },
            "candidates": {
                "total": EvidenceCandidate.objects.count(),
                "exposed": EvidenceCandidate.objects.exclude(exposed_at=None).count(),
                "unexposed": EvidenceCandidate.objects.filter(exposed_at=None).count(),
            },
            "experiments": experiments,
        }
        self.stdout.write(json.dumps(report, default=str, sort_keys=True))
