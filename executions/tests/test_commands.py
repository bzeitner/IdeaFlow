import json
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from executions.management.commands.phase4_reconcile import PHASE4_WORKFLOWS
from executions.models import (
    CutoverMode, DeterministicJob, MeasurementStatus, OutcomeEvent,
    ServicePrincipal, WorkflowCutover,
)
from executions.services import (
    canonical_hash, complete_run, fail_run, fail_trace, start_run, start_trace,
)
from executions.storage import ExecutionPayloadStore
from executions.tests.helpers import make_configuration, make_workflow_version
from ideas.models import Artifact, Category, Idea


class CreateExecutionPrincipalTests(TestCase):
    def test_creates_hashed_one_time_token(self):
        output = StringIO()
        call_command(
            "create_execution_principal", "worker", "--scope", "execution:write",
            stdout=output,
        )
        token = output.getvalue().strip().splitlines()[-1]
        principal = ServicePrincipal.objects.get(name="worker")
        self.assertNotEqual(principal.token_hash, token)
        self.assertEqual(principal.token_hash, ServicePrincipal.hash_token(token))
        self.assertEqual(principal.scopes, ["execution:write"])


class Phase4CommandTests(TestCase):
    def setUp(self):
        self.configuration = make_configuration()

    def run_reconcile(self, *args):
        output = StringIO()
        call_command("phase4_reconcile", *args, stdout=output, stderr=StringIO())
        return json.loads(output.getvalue())

    def make_completed_run(
        self, workflow_key="research", *, input_ref="", output_ref="",
        measurement_status=MeasurementStatus.COMPLETE, reasons=None,
    ):
        workflow = make_workflow_version(workflow_key)
        trace, _ = start_trace(workflow, trigger="test")
        run, _ = start_run(
            trace,
            self.configuration,
            rendered_input_hash=canonical_hash("prompt"),
            rendered_input_ref=input_ref,
        )
        complete_run(
            run,
            output_hash=canonical_hash("answer"),
            output_ref=output_ref,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            cost_micros=2,
            cost_source="price_table",
            measurement_status=measurement_status,
            measurement_unavailable_reasons=reasons,
            finalize_trace=True,
        )
        return run

    def make_idea(self):
        category, _ = Category.objects.get_or_create(
            slug="test", defaults={"name": "Test"}
        )
        return Idea.objects.create(title="Test idea", category=category)

    def test_reconcile_emits_machine_readable_report(self):
        report = self.run_reconcile()
        self.assertIn("provenance", report)
        self.assertIn("audit", report)
        self.assertIn("cutovers", report)
        self.assertEqual(report["schema_version"], "r4.1-reconciliation-v1")
        self.assertIn("workflows", report)
        self.assertIn("projection_attribution", report)
        self.assertIn("measurements", report)
        self.assertIn("payload_storage", report)
        self.assertIn("readiness", report)

    def test_reconcile_reports_complete_run_coverage_by_workflow(self):
        self.make_completed_run()
        report = self.run_reconcile()

        self.assertEqual(report["measurements"]["tokens"]["percent"], 100.0)
        self.assertEqual(report["measurements"]["cost"]["percent"], 100.0)
        self.assertEqual(report["measurements"]["timing"]["percent"], 100.0)
        self.assertEqual(
            report["workflows"]["research"]["trace_completeness_percent"],
            100.0,
        )
        diagnostic = report["workflows"]["research"]["projection_attribution"]
        self.assertEqual(diagnostic["without_projection"], 1)
        self.assertFalse(diagnostic["readiness_gating"])
        self.assertNotIn(
            "successful_run_projection_attribution",
            {check["key"] for check in report["checks"]},
        )

    def test_since_excludes_legacy_projections(self):
        old = Artifact.objects.create(
            idea=self.make_idea(), title="Old", kind=Artifact.Kind.SUMMARY
        )
        recent = Artifact.objects.create(
            idea=self.make_idea(), title="Recent", kind=Artifact.Kind.SUMMARY
        )
        cutoff = timezone.now() - timedelta(days=1)
        Artifact.objects.filter(pk=old.pk).update(
            created_at=cutoff - timedelta(seconds=1)
        )
        Artifact.objects.filter(pk=recent.pk).update(
            created_at=cutoff + timedelta(seconds=1)
        )

        report = self.run_reconcile("--since", cutoff.isoformat())

        artifacts = report["projection_attribution"]["by_projection_type"]["artifacts"]
        self.assertEqual(artifacts["total"], 1)
        self.assertEqual(artifacts["unattributed"], 1)

    def test_audit_counters_use_the_same_window(self):
        idea = self.make_idea()
        cutoff = timezone.now() - timedelta(days=1)
        old_job = DeterministicJob.objects.create(kind="old")
        DeterministicJob.objects.create(kind="recent")
        DeterministicJob.objects.filter(pk=old_job.pk).update(
            queued_at=cutoff - timedelta(seconds=1)
        )
        OutcomeEvent.objects.create(
            idea=idea, event_type="old", occurred_at=cutoff - timedelta(seconds=1)
        )
        OutcomeEvent.objects.create(
            idea=idea, event_type="recent", occurred_at=cutoff + timedelta(seconds=1)
        )

        report = self.run_reconcile("--since", cutoff.isoformat())

        self.assertEqual(report["audit"]["scope"], "audit_window")
        self.assertEqual(report["audit"]["deterministic_jobs"], 1)
        self.assertEqual(report["audit"]["outcome_events"], 1)

    def test_projection_attribution_threshold_fails_for_missing_producer(self):
        run = self.make_completed_run()
        idea = self.make_idea()
        Artifact.objects.create(idea=idea, title="Attributed", produced_by_run=run)
        Artifact.objects.create(
            idea=idea, title="Missing producer", kind=Artifact.Kind.SUMMARY
        )

        report = self.run_reconcile()
        check = next(
            row for row in report["checks"] if row["key"] == "projection_attribution"
        )

        self.assertEqual(report["projection_attribution"]["percent"], 50.0)
        self.assertEqual(check["status"], "fail")

    def test_manual_artifact_is_not_an_ai_projection_candidate(self):
        Artifact.objects.create(idea=self.make_idea(), title="Human upload")
        report = self.run_reconcile()
        artifacts = report["projection_attribution"]["by_projection_type"]["artifacts"]
        self.assertEqual(artifacts["total"], 0)

    def test_projection_linked_to_failed_run_is_not_validly_attributed(self):
        workflow = make_workflow_version("summary")
        trace, _ = start_trace(workflow, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        fail_run(
            run,
            error_class="ProviderError",
            measurement_unavailable_reasons=["provider_request_failed"],
        )
        fail_trace(trace, reason="provider failed")
        Artifact.objects.create(
            idea=self.make_idea(), title="Invalid", kind=Artifact.Kind.SUMMARY,
            produced_by_run=run,
        )

        report = self.run_reconcile()
        artifacts = report["projection_attribution"]["by_projection_type"]["artifacts"]
        self.assertEqual(artifacts["attributed"], 0)
        self.assertEqual(artifacts["invalid_producer"], 1)

    def test_payload_report_detects_missing_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as payload_root, override_settings(
            IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS=True,
            IDEAFLOW_EXECUTION_PAYLOAD_ROOT=payload_root,
        ):
            stored = ExecutionPayloadStore().put("prompt", "different prompt")
            self.make_completed_run(
                input_ref=stored.reference,
                output_ref="execution://missing-output.payload",
            )
            report = self.run_reconcile()

        self.assertEqual(report["payload_storage"]["hash_mismatch"], 1)
        self.assertEqual(report["payload_storage"]["missing"], 1)
        self.assertFalse(report["payload_storage"]["healthy"])

    @override_settings(IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS=False)
    def test_payload_report_allows_uncaptured_payloads_when_capture_disabled(self):
        self.make_completed_run()
        report = self.run_reconcile()
        self.assertEqual(report["payload_storage"]["not_captured"], 2)
        self.assertTrue(report["payload_storage"]["healthy"])

    def test_rollback_evidence_and_cutover_checks_pass_when_complete(self):
        for key in PHASE4_WORKFLOWS:
            WorkflowCutover.objects.update_or_create(
                workflow_key=key, defaults={"mode": CutoverMode.SHADOW}
            )
        evidence = {
            key: {
                "owner": "ops@example.com",
                "tested_at": "2026-09-02T18:30:00Z",
                "result": "pass",
            }
            for key in PHASE4_WORKFLOWS
        }
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "rollback.json"
            filename.write_text(json.dumps(evidence))
            report = self.run_reconcile("--rollback-evidence", str(filename))

        checks = {row["key"]: row["status"] for row in report["checks"]}
        self.assertEqual(checks["cutover_records"], "pass")
        self.assertEqual(checks["rollback_evidence"], "pass")

    def test_missing_cutovers_and_rollback_evidence_fail(self):
        WorkflowCutover.objects.filter(workflow_key__in=PHASE4_WORKFLOWS).delete()
        report = self.run_reconcile()
        checks = {row["key"]: row["status"] for row in report["checks"]}
        self.assertEqual(checks["cutover_records"], "fail")
        self.assertEqual(checks["rollback_evidence"], "fail")

    def test_reconcile_rejects_malformed_rollback_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "rollback.json"
            filename.write_text("not-json")
            with self.assertRaisesMessage(CommandError, "Cannot read rollback evidence"):
                call_command("phase4_reconcile", "--rollback-evidence", str(filename))

    def test_reconcile_validates_rollback_owner_timestamp_and_window(self):
        now = timezone.now()
        cases = (
            ({"owner": " ", "tested_at": now.isoformat(), "result": "pass"}, "owner"),
            ({"owner": "ops", "tested_at": "not-a-date", "result": "pass"}, "tested_at"),
            ({
                "owner": "ops",
                "tested_at": (now + timedelta(days=1)).isoformat(),
                "result": "pass",
            }, "future"),
            ({"owner": "ops", "tested_at": now.isoformat(), "result": "maybe"}, "result"),
        )
        for evidence, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                filename = Path(directory) / "rollback.json"
                filename.write_text(json.dumps({"execute": evidence}))
                with self.assertRaisesMessage(CommandError, message):
                    call_command(
                        "phase4_reconcile", "--rollback-evidence", str(filename)
                    )

        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "rollback.json"
            filename.write_text(json.dumps({
                "execute": {
                    "owner": "ops",
                    "tested_at": (now - timedelta(days=2)).isoformat(),
                    "result": "pass",
                }
            }))
            with self.assertRaisesMessage(CommandError, "predates"):
                call_command(
                    "phase4_reconcile",
                    "--since", (now - timedelta(days=1)).isoformat(),
                    "--rollback-evidence", str(filename),
                )

    def test_fail_on_issues_raises_after_emitting_report(self):
        output = StringIO()
        with self.assertRaisesMessage(CommandError, "did not pass"):
            call_command(
                "phase4_reconcile", "--fail-on-issues",
                stdout=output, stderr=StringIO(),
            )
        self.assertEqual(json.loads(output.getvalue())["readiness"]["status"], "fail")

    def test_failed_run_reason_only_covers_the_measurement_it_explains(self):
        workflow = make_workflow_version("failed-workflow")
        trace, _ = start_trace(workflow, trigger="test")
        run, _ = start_run(
            trace, self.configuration, rendered_input_hash=canonical_hash("prompt")
        )
        fail_run(
            run,
            error_class="ProviderError",
            measurement_unavailable_reasons=["provider_usage_unavailable"],
        )
        fail_trace(trace, reason="provider failed")

        report = self.run_reconcile()

        self.assertEqual(report["measurements"]["tokens"]["percent"], 100.0)
        self.assertEqual(report["measurements"]["cost"]["percent"], 0.0)
        self.assertEqual(report["measurements"]["timing"]["percent"], 100.0)
        self.assertEqual(report["measurements"]["unavailable_reason"]["percent"], 100.0)

    def test_reconcile_rejects_invalid_since(self):
        with self.assertRaisesMessage(CommandError, "ISO-8601"):
            call_command("phase4_reconcile", "--since", "not-a-date")

    def test_cutover_requires_confirmation_for_authoritative_mode(self):
        with self.assertRaisesMessage(CommandError, "require --confirm"):
            call_command(
                "set_workflow_cutover", "execute", "authoritative",
                "--reason", "test",
            )
        call_command(
            "set_workflow_cutover", "execute", "authoritative",
            "--reason", "tested", "--confirm", stdout=StringIO(),
        )
        self.assertEqual(
            WorkflowCutover.objects.get(workflow_key="execute").mode,
            CutoverMode.AUTHORITATIVE,
        )
