from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ideas.feeds import ingest_entries, link_feed, record_feed_item_summary
from ideas.models import (
    FeedItemAssessment, IdeaRelation, RelationType, RepeatResult,
)
from ideas.tests.helpers import make_feed, make_feed_item, make_idea
from sources.importers import import_legacy_phase3
from sources.models import (
    EvidenceAction, EvidenceAssignment, EvidenceCandidate, EvidenceExperiment,
    EvidenceObservation, LegacyEntitySnapshot, Source, SourceItem, Subscription,
)
from sources.services import record_action


PHASE3_FLAGS = {
    "instrumentation": True,
    "gateway": False,
    "projections": False,
    "feedback": False,
    "experiments": True,
}


@override_settings(IDEAFLOW_SOURCES_PHASE3_ENABLED=True, IDEAFLOW_EXECUTION_FLAGS=PHASE3_FLAGS)
class Phase3LegacyImportTests(TestCase):
    def setUp(self):
        self.idea = make_idea(title="Solar storage research")
        self.feed = make_feed(title="Energy feed")
        self.link = link_feed(self.idea, self.feed, rating=4)
        self.item = make_feed_item(
            feed=self.feed,
            title="Battery storage advances",
            content="New grid battery evidence.",
            content_hash="a" * 64,
        )
        FeedItemAssessment.objects.create(
            idea=self.idea, item=self.item, usefulness=5, relevance_note="Direct evidence."
        )
        target = make_idea(title="Grid planning")
        IdeaRelation.objects.create(
            source=self.idea, target=target, relation_type=RelationType.SUPPORTS,
            provenance="agent",
        )
        RepeatResult.objects.create(
            idea=self.idea, title="Storage report", status="actioned"
        )

    def test_import_is_idempotent_and_never_enqueues_historical_items(self):
        first = import_legacy_phase3()
        second = import_legacy_phase3()

        self.assertEqual(first, second)
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(Subscription.objects.count(), 1)
        source_item = SourceItem.objects.get(legacy_feed_item=self.item)
        self.assertFalse(source_item.eligible_for_processing)
        self.assertEqual(EvidenceAction.objects.get().action, "useful")
        self.assertEqual(EvidenceAssignment.objects.count(), 0)

    def test_import_snapshots_graph_and_repeat_decisions_with_provenance(self):
        import_legacy_phase3()

        relation = LegacyEntitySnapshot.objects.get(entity_type="idea_relation")
        repeat = LegacyEntitySnapshot.objects.get(entity_type="repeat_result")
        self.assertEqual(relation.payload["provenance"], "agent")
        self.assertEqual(repeat.payload["status"], "actioned")
        self.assertEqual(repeat.provenance, "legacy_import")

    def test_dry_run_does_not_write(self):
        Subscription.objects.all().delete()
        Source.objects.all().delete()
        call_command("import_phase3_legacy", "--dry-run")
        self.assertFalse(Source.objects.exists())

    def test_experiment_command_starts_new_item_enrollment(self):
        call_command(
            "start_evidence_experiment",
            "ranking-v1",
            hypothesis="Semantic overlap improves precision.",
            treatment_percent=40,
            minimum_sample_size=20,
        )
        experiment = EvidenceExperiment.objects.get(key="ranking-v1")
        self.assertEqual(experiment.state, EvidenceExperiment.State.RUNNING)
        self.assertEqual(experiment.treatment_percent, 40)
        self.assertIsNotNone(experiment.enrollment_started_at)


@override_settings(
    IDEAFLOW_SOURCES_PHASE3_ENABLED=True,
    IDEAFLOW_EXECUTION_FLAGS=PHASE3_FLAGS,
    IDEAFLOW_API_TOKEN="phase3-test-token",
)
class NewEvidenceExperimentTests(TestCase):
    def setUp(self):
        self.idea = make_idea(title="Solar battery economics", summary="Storage cost evidence")
        self.feed = make_feed(title="Energy economics")
        link_feed(self.idea, self.feed, rating=5)
        self.experiment = EvidenceExperiment.objects.create(
            key="new-evidence-ranking",
            hypothesis="Overlap prefilter improves useful evidence precision.",
            salt="test-only-salt",
            state=EvidenceExperiment.State.RUNNING,
            enrollment_started_at=timezone.now(),
            treatment_percent=50,
            minimum_sample_size=10,
        )

    def test_new_ingress_creates_candidate_and_sticky_assignment(self):
        created = ingest_entries(
            self.feed,
            [{
                "id": "new-1",
                "link": "https://example.com/new-1",
                "title": "Solar battery storage costs fall",
                "summary": "New evidence about storage economics.",
            }],
        )

        self.assertEqual(len(created), 1)
        source_item = SourceItem.objects.get(legacy_feed_item=created[0])
        candidate = EvidenceCandidate.objects.get(source_item=source_item, idea=self.idea)
        assignment = EvidenceAssignment.objects.get(candidate=candidate)
        self.assertTrue(source_item.eligible_for_processing)
        self.assertGreater(candidate.deterministic_score, 0)
        self.assertIn(assignment.variant, {"control", "treatment"})

        # Re-ingesting the same external identity creates neither a second item
        # nor a crossover assignment.
        self.assertEqual(ingest_entries(self.feed, [{"id": "new-1"}]), [])
        self.assertEqual(EvidenceAssignment.objects.count(), 1)

    def test_feedback_creates_raw_experiment_observation(self):
        ingest_entries(
            self.feed,
            [{"id": "new-2", "title": "Battery cost evidence", "summary": "Solar storage"}],
        )
        candidate = EvidenceCandidate.objects.get()

        action = record_action(candidate, EvidenceAction.Action.SAVED)

        observation = EvidenceObservation.objects.get(action=action)
        self.assertEqual(observation.metric, "useful")
        self.assertEqual(observation.value, 1.0)

    def test_existing_feed_assessment_becomes_experiment_feedback(self):
        item = ingest_entries(
            self.feed,
            [{"id": "new-feedback", "title": "Battery evidence", "summary": "Solar storage"}],
        )[0]

        record_feed_item_summary(
            item,
            summary="Useful evidence.",
            idea=self.idea,
            usefulness=5,
            relevance_note="Directly relevant.",
        )

        action = EvidenceAction.objects.get(legacy_assessment__item=item)
        self.assertEqual(action.action, EvidenceAction.Action.USEFUL)
        self.assertEqual(action.observations.get().value, 1.0)

    def test_phase3_queue_exposes_only_new_ranked_candidates(self):
        historical = make_feed_item(feed=self.feed, guid="historical", title="Old backlog")
        new_item = ingest_entries(
            self.feed,
            [{"id": "new-queue", "title": "Solar storage evidence"}],
        )[0]

        response = self.client.get(
            f"/api/feed-items/?idea={self.idea.pk}&unassessed=1&content=1",
            HTTP_AUTHORIZATION="Bearer phase3-test-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["id"] for row in response.json()["items"]],
            [new_item.pk],
        )
        evidence = response.json()["items"][0]["evidence"]
        self.assertEqual(evidence["experiment"]["key"], self.experiment.key)
        self.assertIn(evidence["experiment"]["variant"], {"control", "treatment"})
        candidate = EvidenceCandidate.objects.get(source_item__legacy_feed_item=new_item)
        self.assertIsNotNone(candidate.exposed_at)
        self.assertNotEqual(historical.pk, candidate.source_item.legacy_feed_item_id)

    def test_items_before_enrollment_are_not_assigned(self):
        self.experiment.enrollment_started_at = timezone.now() + timedelta(days=1)
        self.experiment.save(update_fields=["enrollment_started_at"])

        ingest_entries(self.feed, [{"id": "new-3", "title": "Future experiment item"}])

        self.assertEqual(EvidenceCandidate.objects.count(), 1)
        self.assertFalse(EvidenceAssignment.objects.exists())
