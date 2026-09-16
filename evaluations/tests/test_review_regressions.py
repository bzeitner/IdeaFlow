import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, connections, transaction
from django.test import TestCase, override_settings, skipUnlessDBFeature

from evaluations.models import EvaluationResult, EvaluatorVersion, MetricDefinition
from evaluations.seeds import seed_evaluators
from evaluations.services import evaluate_run, record_result
from evaluations.validation import summarize
from executions.models import ExecutionTrace, LLMRun
from executions.services import canonical_hash, complete_run, start_run, start_trace
from executions.tests.helpers import make_configuration, make_workflow_version

from .base import AuditTransactionTestCase, audit_fixture_reset
from .helpers import approved_excerpt


def make_completed_run():
    trace, _ = start_trace(make_workflow_version(), trigger="test")
    run, _ = start_run(trace, make_configuration(), rendered_input_hash=canonical_hash("prompt"))
    output = b"Supported finding."
    complete_run(run, output_hash=canonical_hash(output), finish_reason="stop",
                 usage={"total_tokens": 1}, cost_micros=1, cost_source="test",
                 measurement_status="complete", finalize_trace=True)
    run.refresh_from_db()
    return run, output


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"evaluators": True})
class EvidenceRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.progress, cls.quality, cls.structure = seed_evaluators()
        cls.target_run, cls.output = make_completed_run()

    def values(self, version, status="pass", *, include_inputs=True):
        result, _ = evaluate_run(self.target_run, self.structure, actor_label="operator",
                                 idempotency_key="baseline", output=self.output)
        manifest = copy.deepcopy(result.input_manifest)
        manifest["evaluator_hash"] = version.content_hash
        if include_inputs:
            snapshots = {
                "objective": "Resolve the frozen question.",
                "source_evidence": [{"reference": "internal:test", "observation": "Supported finding."}],
                "execution_evidence": [{"event_id": "test-event", "observation": "Required inspection recorded."}],
                "frozen_requirements": ["Address the frozen question."],
            }
            for kind, value in snapshots.items():
                manifest["evidence"][kind] = approved_excerpt(kind, value)
        rows = [{"id": c["id"], "status": status, "reason": "Recorded assessment.",
                 "evidence_refs": list(manifest["evidence"]) if status in {"pass", "fail"} else []}
                for c in version.rubric["criteria"]]
        return {
            "evaluator_version": version, "evaluated_run": self.target_run,
            "output_hash": self.target_run.output_hash, "input_manifest": manifest,
            "input_manifest_hash": canonical_hash(manifest), "criterion_results": rows,
            "summary": summarize(version.rubric, rows), "actor_label": "operator",
            "idempotency_key": "review-result",
        }

    def test_quality_without_required_inputs_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "lacks required evidence"):
            record_result(**self.values(self.quality, include_inputs=False))

    def test_complete_typed_quality_evidence_is_accepted(self):
        result, _ = record_result(**self.values(self.quality))
        self.assertEqual(result.summary["counts"]["pass_rate"], 1)

    def test_empty_observed_sources_are_distinct_from_unavailable_sources(self):
        values = self.values(self.quality, status="fail")
        values["input_manifest"]["evidence"]["source_evidence"] = approved_excerpt("source_evidence", [])
        values["input_manifest_hash"] = canonical_hash(values["input_manifest"])
        result, _ = record_result(**values)
        self.assertEqual(result.summary["counts"]["fail"], 7)

    def test_available_evidence_must_be_cited_by_the_judgment(self):
        values = self.values(self.quality)
        values["criterion_results"][0]["evidence_refs"] = ["output"]
        with self.assertRaisesMessage(ValidationError, "lacks required evidence"):
            record_result(**values)

    def test_typed_evidence_cannot_be_a_mislabeled_hash_or_modified_snapshot(self):
        for mutation in ("missing_value", "changed_value", "wrong_type"):
            values = self.values(self.quality)
            source = values["input_manifest"]["evidence"]["source_evidence"]
            if mutation == "missing_value":
                source.pop("value")
            elif mutation == "changed_value":
                source["value"] = []
            else:
                source.update(value="a report is not source evidence", hash=canonical_hash("a report is not source evidence"))
            values["input_manifest_hash"] = canonical_hash(values["input_manifest"])
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                record_result(**values)

    def test_evaluator_level_required_inputs_are_enforced(self):
        version_values = {f.name: getattr(self.structure, f.name) for f in self.structure._meta.concrete_fields
                          if f.name not in {"id", "content_hash", "created_at"}}
        version_values.update(version=2, required_inputs=["output", "execution_evidence"])
        version = EvaluatorVersion.objects.create(**version_values)
        with self.assertRaisesMessage(ValidationError, "execution_evidence"):
            evaluate_run(self.target_run, version, actor_label="operator", idempotency_key="requires-events", output=self.output)

    def test_abstention_is_saved_without_score_and_cannot_have_one(self):
        for state in ("insufficient_evidence", "not_applicable"):
            values = self.values(self.progress, state, include_inputs=False)
            values.update(progress_score=None, idempotency_key=state)
            result, _ = record_result(**values)
            self.assertIsNone(result.progress_score)
            self.assertIsNone(result.summary["counts"]["pass_rate"])
            with self.assertRaisesMessage(ValidationError, "must abstain"):
                record_result(**{**values, "idempotency_key": state + "-bad", "progress_score": 5})

    def test_completed_progress_still_requires_score_and_frozen_objective(self):
        values = self.values(self.progress)
        with self.assertRaises(ValidationError):
            record_result(**values)
        result, _ = record_result(**{**values, "progress_score": 5})
        self.assertEqual(result.progress_score, 5)

    def test_unsaved_rubric_cannot_change_persisted_result_interpretation(self):
        values = self.values(self.quality, "fail")
        modified = copy.deepcopy(self.quality)
        modified.rubric["criteria"][0]["severity"] = "critical"
        values.update(evaluator_version=modified, summary=summarize(modified.rubric, values["criterion_results"]))
        with self.assertRaisesMessage(ValidationError, "Summary does not match"):
            record_result(**values)
        with self.assertRaisesMessage(ValidationError, "Summary does not match"):
            EvaluationResult.objects.create(**values)

    def test_unsaved_target_run_cannot_change_output_identity(self):
        values = self.values(self.quality)
        modified = copy.deepcopy(self.target_run)
        modified.output_hash = canonical_hash("different output")
        values.update(evaluated_run=modified, output_hash=modified.output_hash)
        values["input_manifest"]["output_hash"] = modified.output_hash
        values["input_manifest"]["evidence"]["output"]["hash"] = modified.output_hash
        values["input_manifest_hash"] = canonical_hash(values["input_manifest"])
        with self.assertRaisesMessage(ValidationError, "frozen output hash"):
            record_result(**values)

    def test_evaluate_reloads_the_stored_rubric(self):
        modified = copy.deepcopy(self.structure)
        modified.rubric["criteria"][0]["severity"] = "critical"
        modified.rubric["criteria"][0]["id"] = "unsaved-id"
        result, _ = evaluate_run(self.target_run, modified, actor_label="operator",
                                 idempotency_key="stored-only", output=self.output)
        self.assertEqual(result.criterion_results[0]["id"], "output.nonempty")
        self.assertEqual(result.evaluator_version.rubric, self.structure.rubric)

    def test_nullable_subject_is_supported_on_the_actual_backend(self):
        self.assertIsNone(ExecutionTrace.objects.get(pk=self.target_run.trace_id).subject_content_type_id)
        result, _ = evaluate_run(self.target_run, self.structure, actor_label="operator",
                                 idempotency_key="nullable-subject", output=self.output)
        self.assertEqual(result.evaluated_run_id, self.target_run.pk)


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"evaluators": True})
class FixtureResetRegressionTests(AuditTransactionTestCase):
    def assert_guards_active(self):
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE evaluations_metricdefinition SET actor_label = 'changed'")
        if connection.vendor == "postgresql":
            with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("TRUNCATE evaluations_metricdefinition CASCADE")

    def test_fixture_reset_flushes_seeded_tables_and_restores_guards(self):
        seed_evaluators()
        self.assert_guards_active()
        self._fixture_teardown()
        self.assertEqual(MetricDefinition.objects.count(), 0)
        seed_evaluators()
        self.assert_guards_active()

    def test_failed_reset_rolls_back_guard_removal_and_data_changes(self):
        seed_evaluators()
        with self.assertRaisesMessage(RuntimeError, "test failure"):
            with audit_fixture_reset("default", connection.settings_dict["NAME"]):
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE evaluations_metricdefinition SET actor_label = 'changed'")
                raise RuntimeError("test failure")
        self.assertFalse(MetricDefinition.objects.filter(actor_label="changed").exists())
        self.assert_guards_active()

    def test_reset_refuses_non_test_context_or_a_different_database(self):
        with patch("evaluations.tests.base._TestState", object()):
            with self.assertRaises(RuntimeError), audit_fixture_reset("default", connection.settings_dict["NAME"]):
                self.fail("Reset ran outside the test environment")
        with self.assertRaises(RuntimeError), audit_fixture_reset("default", "not-this-database"):
            self.fail("Reset ran against the wrong database")

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_identical_requests_create_one_result(self):
        _, _, version = seed_evaluators()
        run, output = make_completed_run()
        barrier = Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=10)
                result, created = evaluate_run(run, version, actor_label="operator",
                                               idempotency_key="concurrent", output=output)
                return result.pk, created
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(len({pk for pk, _ in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(EvaluationResult.objects.count(), 1)
