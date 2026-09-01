import json

from django.core.management.base import BaseCommand
from django.db.models import Q

from executions.models import (
    ArtifactVersion, DeterministicJob, LLMRun, OutcomeEvent, WorkflowCutover,
)
from ideas.models import (
    Artifact, Episode, EpisodeRun, PersonaReview, PersonaVote,
    RelationshipCouncilReview, RelationshipCouncilVote, WeeklySummary,
)


class Command(BaseCommand):
    help = "Report Phase 4 provenance, vertical migration, and cutover readiness."

    def handle(self, *args, **options):
        report = {
            "cutovers": dict(WorkflowCutover.objects.values_list("workflow_key", "mode")),
            "provenance": {
                "persona_reviews": PersonaReview.objects.count(),
                "persona_reviews_attributed": PersonaReview.objects.exclude(produced_by_run=None).count(),
                "persona_votes": PersonaVote.objects.count(),
                "persona_votes_attributed": PersonaVote.objects.exclude(produced_by_run=None).count(),
                "relationship_reviews": RelationshipCouncilReview.objects.count(),
                "relationship_reviews_attributed": RelationshipCouncilReview.objects.exclude(
                    produced_by_run=None
                ).count(),
                "relationship_votes": RelationshipCouncilVote.objects.count(),
                "relationship_votes_attributed": RelationshipCouncilVote.objects.exclude(
                    produced_by_run=None
                ).count(),
                "weekly_summaries": WeeklySummary.objects.count(),
                "weekly_summaries_attributed": WeeklySummary.objects.exclude(produced_by_run=None).count(),
                "artifacts": Artifact.objects.count(),
                "artifact_versions": ArtifactVersion.objects.filter(artifact__isnull=False).count(),
                "episodes": Episode.objects.count(),
                "episode_runs": EpisodeRun.objects.count(),
                "episode_runs_traced": EpisodeRun.objects.exclude(execution_trace=None).count(),
                "media_versions": ArtifactVersion.objects.filter(episode__isnull=False).count(),
            },
            "audit": {
                "deterministic_jobs": DeterministicJob.objects.count(),
                "outcome_events": OutcomeEvent.objects.count(),
                "terminal_runs_without_measurement_reason": LLMRun.objects.filter(
                    status__in=["succeeded", "failed"],
                    measurement_status__in=["partial", "unavailable"],
                ).filter(
                    Q(measurement_unavailable_reasons=[]) | Q(measurement_unavailable_reasons__isnull=True)
                ).count(),
            },
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
