from django.core.exceptions import ValidationError
from django.test import TestCase

from executions.models import (
    ArtifactVersion, CutoverMode, DeterministicJob, MeasurementStatus,
    OutcomeEvent, TraceStatus, WorkflowCutover,
)
from executions.services import (
    attach_projection, canonical_hash, complete_run, complete_tool_invocation,
    complete_trace, enforce_projection_write, estimate_cost_micros, fail_run,
    record_artifact_version, record_deterministic_job, record_outcome,
    redact_error_detail, start_run, start_tool_invocation, start_trace,
)

from .helpers import make_configuration, make_workflow_version


class ExecutionServiceTests(TestCase):
    def setUp(self):
        self.workflow_version = make_workflow_version()
        self.configuration = make_configuration()

    def test_trace_and_run_idempotency(self):
        trace, created = start_trace(
            self.workflow_version, trigger="test", idempotency_key="same"
        )
        same_trace, same_created = start_trace(
            self.workflow_version, trigger="test", idempotency_key="same"
        )
        self.assertTrue(created)
        self.assertFalse(same_created)
        self.assertEqual(trace, same_trace)

        run, run_created = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt"),
            idempotency_key="run-same",
        )
        same_run, same_run_created = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt"),
            idempotency_key="run-same",
        )
        self.assertTrue(run_created)
        self.assertFalse(same_run_created)
        self.assertEqual(run, same_run)

    def test_successful_run_requires_explicit_trace_completion(self):
        trace, _ = start_trace(self.workflow_version, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        complete_run(
            run,
            output_hash=canonical_hash("answer"),
            usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )
        trace.refresh_from_db()
        self.assertEqual(trace.status, TraceStatus.RUNNING)
        complete_trace(trace)
        trace.refresh_from_db()
        self.assertEqual(trace.status, TraceStatus.SUCCEEDED)
        self.assertEqual(
            list(trace.events.values_list("event_type", flat=True)),
            ["trace.queued", "run.started", "run.succeeded", "trace.succeeded"],
        )

    def test_partial_measurement_requires_reason(self):
        trace, _ = start_trace(self.workflow_version, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        with self.assertRaises(ValidationError):
            complete_run(
                run, output_hash=canonical_hash("answer"),
                measurement_status=MeasurementStatus.PARTIAL,
            )

    def test_failed_run_is_idempotent(self):
        trace, _ = start_trace(self.workflow_version, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        failed, changed = fail_run(
            run, error_class="ProviderError", error_detail="token=do-not-store",
            measurement_unavailable_reasons=["provider_usage_missing"],
        )
        again, changed_again = fail_run(
            failed, error_class="Other",
            measurement_unavailable_reasons=["provider_usage_missing"],
        )
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(again.error_class, "ProviderError")
        self.assertNotIn("do-not-store", again.error_detail)

    def test_tool_invocation_is_recorded_under_run(self):
        trace, _ = start_trace(self.workflow_version, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        tool, created = start_tool_invocation(
            run, tool_name="search", idempotency_key="search-1"
        )
        completed, changed = complete_tool_invocation(
            tool, response_hash=canonical_hash("result")
        )
        self.assertTrue(created)
        self.assertTrue(changed)
        self.assertEqual(completed.status, TraceStatus.SUCCEEDED)

    def test_projection_is_attributed_and_subject_mismatch_is_rejected(self):
        from ideas.reporting import record_effort
        from ideas.tests.helpers import make_idea

        idea = make_idea(title="Subject")
        other = make_idea(title="Other")
        entry, _ = record_effort(other, topic="Result", model="other")
        trace, _ = start_trace(self.workflow_version, trigger="test", subject=idea)
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        with self.assertRaises(ValidationError):
            attach_projection(entry, run)
        entry.idea = idea
        entry.save(update_fields=["idea"])
        attach_projection(entry, run)
        entry.refresh_from_db()
        self.assertEqual(entry.produced_by_run, run)

    def test_seeded_workflows_are_available(self):
        from executions.models import ApprovalStatus, WorkflowVersion

        self.assertTrue(
            WorkflowVersion.objects.filter(
                workflow__key="feed_score", version=1,
                status=ApprovalStatus.APPROVED,
            ).exists()
        )

    def test_authoritative_cutover_requires_an_execution_run(self):
        WorkflowCutover.objects.update_or_create(
            workflow_key="research", defaults={"mode": CutoverMode.AUTHORITATIVE}
        )
        with self.assertRaises(ValidationError):
            enforce_projection_write("research")

        trace, _ = start_trace(self.workflow_version, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        self.assertEqual(enforce_projection_write("research", run), CutoverMode.AUTHORITATIVE)

    def test_deterministic_job_and_outcome_are_idempotent(self):
        from ideas.tests.helpers import make_idea

        idea = make_idea()
        first, created = record_deterministic_job(
            "repository.pr_reconcile", idempotency_key="pr-1",
            input_value={"state": "MERGED"}, output_value={"changed": True},
        )
        same, created_again = record_deterministic_job(
            "repository.pr_reconcile", idempotency_key="pr-1"
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, same)
        self.assertEqual(first.status, TraceStatus.SUCCEEDED)

        outcome, outcome_created = record_outcome(
            idea, "repository.pr_merged", idempotency_key="pr-1"
        )
        same_outcome, same_outcome_created = record_outcome(
            idea, "repository.pr_merged", idempotency_key="pr-1"
        )
        self.assertTrue(outcome_created)
        self.assertFalse(same_outcome_created)
        self.assertEqual(outcome, same_outcome)
        self.assertEqual(DeterministicJob.objects.count(), 1)
        self.assertEqual(OutcomeEvent.objects.count(), 1)

    def test_artifact_versions_form_an_immutable_chain(self):
        from ideas.models import Artifact
        from ideas.tests.helpers import make_idea

        artifact = Artifact.objects.create(idea=make_idea(), title="Report", url="https://one")
        first, _ = record_artifact_version(
            artifact=artifact, media_type="text/uri-list",
            checksum_sha256=canonical_hash("https://one"), storage_key="https://one",
        )
        duplicate, created = record_artifact_version(
            artifact=artifact, media_type="text/uri-list",
            checksum_sha256=canonical_hash("https://one"), storage_key="https://one",
        )
        second, _ = record_artifact_version(
            artifact=artifact, media_type="text/uri-list",
            checksum_sha256=canonical_hash("https://two"), storage_key="https://two",
        )
        self.assertFalse(created)
        self.assertEqual(duplicate, first)
        self.assertEqual(second.supersedes, first)
        self.assertEqual(ArtifactVersion.objects.count(), 2)


class CostTests(TestCase):
    def test_cost_rounds_up_to_currency_micro(self):
        from django.utils import timezone
        from executions.models import PricingVersion

        pricing = PricingVersion.objects.create(
            provider="test", model_identifier="test", effective_from=timezone.now(),
            input_micros_per_million=2_000_000,
            output_micros_per_million=4_000_000,
        )
        configuration = make_configuration()
        configuration = type(configuration).objects.get(pk=configuration.pk)
        type(configuration).objects.filter(pk=configuration.pk).update(pricing_version=pricing)
        configuration.refresh_from_db()
        self.assertEqual(
            estimate_cost_micros(configuration, {"input_tokens": 500, "output_tokens": 250}),
            2000,
        )


class RedactionTests(TestCase):
    def test_redacts_common_credentials(self):
        value = redact_error_detail(
            "Authorization: Bearer abc123 api_key=secret password=hunter2"
        )
        self.assertNotIn("abc123", value)
        self.assertNotIn("secret", value)
        self.assertNotIn("hunter2", value)
