import copy
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings

from evaluations.admin import ResultAdmin
from evaluations.deterministic import evaluate
from evaluations.models import EvaluationResult, EvaluatorApproval, EvaluatorDefinition, EvaluatorVersion, MetricDefinition
from evaluations.seeds import seed_evaluators
from evaluations.services import evaluate_run, record_result
from evaluations.validation import summarize
from executions.models import LLMRun
from executions.services import canonical_hash, complete_run, start_run, start_trace
from executions.tests.helpers import make_configuration, make_workflow_version
from ideas.tests.helpers import make_idea
from .helpers import approved_excerpt


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"evaluators": True})
class FoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.progress, cls.quality, cls.structure = seed_evaluators()
        cls.idea = make_idea()
        trace, _ = start_trace(make_workflow_version(), trigger="test", subject=cls.idea)
        cls.target_run, _ = start_run(trace, make_configuration(), rendered_input_hash=canonical_hash("prompt"))
        cls.output = b'{"answer":"Supported conclusion", "count":0}'
        complete_run(cls.target_run, output_hash=canonical_hash(cls.output), finish_reason="stop",
                     usage={"total_tokens": 10}, cost_micros=1, cost_source="test",
                     measurement_status="complete", finalize_trace=True)
        cls.target_run.refresh_from_db()
        cls.contract = {"format": "json_object", "fields": {"answer": "string", "count": "integer"}, "required": ["answer", "count"]}

    def assess(self, **kwargs):
        return evaluate_run(self.target_run, self.structure, actor_label="operator", idempotency_key="case-1",
                            output=self.output, contract=self.contract, **kwargs)

    def result_values(self, result):
        return {f.name: getattr(result, f.name) for f in result._meta.concrete_fields
                if f.name not in {"id", "created_at", "content_hash"}}

    def test_seed_is_idempotent_without_approval_or_model_calls(self):
        hashes = [v.content_hash for v in seed_evaluators()]
        self.assertEqual(hashes, [self.progress.content_hash, self.quality.content_hash, self.structure.content_hash])
        self.assertEqual(MetricDefinition.objects.count(), 3)
        self.assertEqual(EvaluatorApproval.objects.count(), 0)
        self.assertEqual(LLMRun.objects.count(), 1)

    def test_seed_detects_definition_drift(self):
        from unittest.mock import patch
        with patch("evaluations.seeds.ANCHORS", {"1": "changed"}):
            with self.assertRaises(ValidationError):
                seed_evaluators()

    def test_hashes_and_results_are_auditable_without_mutating_generation(self):
        before = LLMRun.objects.values().get(pk=self.target_run.pk)
        result, created = self.assess()
        self.assertTrue(created)
        self.assertEqual(result.output_hash, self.target_run.output_hash)
        self.assertEqual(result.input_manifest_hash, canonical_hash(result.input_manifest))
        self.assertEqual(result.summary["counts"]["pass"], 4)
        self.assertEqual(result.summary["counts"]["not_applicable"], 1)
        self.assertIsNone(result.progress_score)
        self.assertFalse(result.summary["decision_grade"])
        self.assertEqual(before, LLMRun.objects.values().get(pk=self.target_run.pk))
        self.assertEqual(LLMRun.objects.count(), 1)
        self.target_run.trace.refresh_from_db()
        self.assertEqual(self.target_run.trace.status, "succeeded")

    def test_idempotent_command_reuses_frozen_observations(self):
        first, _ = self.assess()
        second, created = self.assess()
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(created)
        values = self.result_values(first)
        self.assertFalse(record_result(**values)[1])
        values["rationale"] = "Conflicting content"
        with self.assertRaises(ValidationError):
            record_result(**values)

    def test_changed_contract_rejects_idempotency_reuse(self):
        self.assess()
        with self.assertRaises(ValidationError):
            evaluate_run(self.target_run, self.structure, actor_label="operator", idempotency_key="case-1",
                         output=self.output, contract={"format": "text"})

    def test_disabled_writes_and_inactive_evaluator(self):
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={"evaluators": False}):
            with self.assertRaises(ValidationError):
                self.assess()
        EvaluatorDefinition.objects.filter(pk=self.structure.evaluator_id).update(is_active=False)
        with self.assertRaises(ValidationError):
            self.assess()
        self.assertEqual(EvaluationResult.objects.count(), 0)

    def test_wrong_output_rejected_even_on_retry(self):
        self.assess()
        with self.assertRaises(ValidationError):
            evaluate_run(self.target_run, self.structure, actor_label="operator", idempotency_key="case-1", output=b"different")

    def test_missing_payload_is_not_negative_quality(self):
        with self.assertRaises(ValidationError):
            evaluate_run(self.target_run, self.structure, actor_label="operator", idempotency_key="missing")
        self.assertFalse(EvaluationResult.objects.exists())

    def test_cross_workflow_is_rejected(self):
        trace, _ = start_trace(make_workflow_version("feed_score"), trigger="test")
        run, _ = start_run(trace, self.target_run.model_configuration, rendered_input_hash=canonical_hash("p"))
        complete_run(run, output_hash=canonical_hash(self.output), measurement_status="unavailable", measurement_unavailable_reasons=["test"])
        with self.assertRaises(ValidationError):
            evaluate_run(run, self.structure, actor_label="operator", idempotency_key="wrong-workflow", output=self.output)

    def test_unknown_reference_fails_without_exposing_other_idea_content(self):
        result, _ = self.assess(references=[{"model": "ideas.artifact", "id": 999999}])
        self.assertEqual(result.criterion_results[-1]["status"], "fail")
        self.assertEqual(result.input_manifest["reference_observations"], [{"model": "ideas.artifact", "id": 999999, "valid": False}])

    def test_reference_allowlist(self):
        with self.assertRaises(ValidationError):
            self.assess(references=[{"model": "auth.user", "id": 1}])

    def test_validation_rejects_wrong_manifest_criteria_scores_and_evidence(self):
        result, _ = self.assess()
        for change in (
            {"output_hash": "a" * 64},
            {"input_manifest_hash": "a" * 64},
            {"progress_score": 5},
            {"summary": {"counts": "invented"}},
            {"criterion_results": []},
        ):
            values = self.result_values(result)
            values.update(change, idempotency_key="invalid")
            with self.subTest(change=change), self.assertRaises(ValidationError):
                record_result(**values)
        rows = copy.deepcopy(result.criterion_results)
        rows[0]["evidence_refs"] = ["unknown"]
        values = self.result_values(result)
        values.update(criterion_results=rows, idempotency_key="invalid")
        with self.assertRaises(ValidationError):
            record_result(**values)

    def test_instance_and_bulk_mutations_blocked_for_all_frozen_models(self):
        result, _ = self.assess()
        approval = EvaluatorApproval.objects.create(evaluator_version=self.structure, decision="rejected", reason="Not calibrated", actor_label="operator")
        for row in (self.structure.metric, self.structure, approval, result):
            with self.subTest(model=type(row).__name__):
                with self.assertRaises(ValidationError):
                    row.save()
                with self.assertRaises(ValidationError):
                    row.delete()
                with self.assertRaises(ValidationError):
                    type(row).objects.filter(pk=row.pk).update(actor_label="changed")
                with self.assertRaises(ValidationError):
                    type(row).objects.filter(pk=row.pk).delete()
                with self.assertRaises(ValidationError):
                    type(row).objects.bulk_update([row], ["actor_label"])

    def test_database_rejects_raw_mutations(self):
        result, _ = self.assess()
        approval = EvaluatorApproval.objects.create(evaluator_version=self.structure, decision="rejected", reason="Not calibrated", actor_label="operator")
        for row in (self.structure.metric, self.structure, approval, result):
            table = connection.ops.quote_name(row._meta.db_table)
            for sql, parameters in ((f"UPDATE {table} SET actor_label = %s WHERE id = %s", ["changed", row.pk]),
                                    (f"DELETE FROM {table} WHERE id = %s", [row.pk])):
                with self.subTest(table=table, sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(sql, parameters)

    def test_unproven_approval_rejected(self):
        with self.assertRaises(ValidationError):
            EvaluatorApproval.objects.create(evaluator_version=self.structure, decision="approved", reason="Looks good", actor_label="operator")

    def test_ordinal_progress_requires_explicit_score(self):
        result, _ = self.assess()
        manifest = copy.deepcopy(result.input_manifest)
        manifest["evaluator_hash"] = self.progress.content_hash
        objective = "Identify a supported conclusion."
        sources = [{"reference": "internal:test", "observation": "Recorded support."}]
        manifest["evidence"]["objective"] = approved_excerpt("objective", objective)
        manifest["evidence"]["sources"] = approved_excerpt("source_evidence", sources)
        rows = [{"id": "progress.objective", "status": "pass", "reason": "Scoped objective resolved.", "evidence_refs": ["output", "objective", "sources"]}]
        values = self.result_values(result)
        values.update(evaluator_version=self.progress, input_manifest=manifest,
                      input_manifest_hash=canonical_hash(manifest), criterion_results=rows,
                      summary=summarize(self.progress.rubric, rows), idempotency_key="progress", progress_score=5)
        progress, _ = record_result(**values)
        self.assertEqual(progress.progress_score, 5)
        for score in (0, 6, True, 3.5, None):
            with self.subTest(score=score), self.assertRaises(ValidationError):
                record_result(**{**values, "progress_score": score, "idempotency_key": "invalid-score"})

    def test_critical_failure_not_hidden_by_presentation(self):
        rows = [{"id": c["id"], "status": "pass", "reason": "Observed", "evidence_refs": ["output"]} for c in self.quality.rubric["criteria"]]
        next(r for r in rows if r["id"] == "evidence.fabrication")["status"] = "fail"
        summary = summarize(self.quality.rubric, rows)
        self.assertEqual(summary["critical_failures"], ["evidence.fabrication"])
        self.assertEqual(summary["counts"]["expected"], 7)
        self.assertEqual(summary["dimensions"]["communication"]["pass_rate"], 1)

    def test_empty_denominator_and_missing_evidence(self):
        rows = [{"id": c["id"], "status": "insufficient_evidence", "reason": "Missing input", "evidence_refs": []} for c in self.quality.rubric["criteria"]]
        summary = summarize(self.quality.rubric, rows)
        self.assertIsNone(summary["counts"]["pass_rate"])
        self.assertEqual(summary["counts"]["judgment_coverage"], 0)

    def test_operator_commands_use_files_and_emit_no_raw_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output.txt"
            output.write_bytes(self.output)
            contract = Path(tmp) / "contract.json"
            contract.write_text(json.dumps(self.contract))
            for expected in (True, False):
                stdout = StringIO()
                call_command("evaluate_research", str(self.target_run.pk), "--actor", "operator",
                             "--idempotency-key", "command", "--output-file", str(output),
                             "--contract-file", str(contract), stdout=stdout)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["created"], expected)
                self.assertNotIn("Supported conclusion", stdout.getvalue())
        stdout = StringIO()
        call_command("seed_evaluators", stdout=stdout)
        self.assertEqual(len(json.loads(stdout.getvalue())), 3)

    def test_command_disabled_returns_error(self):
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={}):
            with self.assertRaises(CommandError):
                call_command("evaluate_research", str(self.target_run.pk), "--actor", "operator", "--idempotency-key", "off")

    def test_admin_cannot_edit_or_add_results(self):
        admin = ResultAdmin(EvaluationResult, AdminSite())
        self.assertFalse(admin.has_add_permission(None))
        self.assertFalse(admin.has_change_permission(None))
        self.assertFalse(admin.has_delete_permission(None))

    def test_direct_result_service_respects_disabled_flag(self):
        result, _ = self.assess()
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={}), self.assertRaises(ValidationError):
            record_result(**self.result_values(result))

    def test_payload_store_reads_verified_bytes_without_changing_run(self):
        from executions.storage import ExecutionPayloadStore
        with tempfile.TemporaryDirectory() as tmp, override_settings(IDEAFLOW_EXECUTION_PAYLOAD_ROOT=tmp):
            store = ExecutionPayloadStore()
            payload = store.put("output", self.output)
            # Existing execution records are intentionally not made immutable by
            # this slice; set the fixture's historical storage reference only.
            LLMRun.objects.filter(pk=self.target_run.pk).update(output_ref=payload.reference)
            result, _ = evaluate_run(self.target_run, self.structure, actor_label="operator",
                                     idempotency_key="stored", store=store)
            self.assertEqual(result.input_manifest["evidence"]["output"]["reference"], payload.reference)

    def test_database_guard_migration_reverses_and_reinstalls(self):
        import importlib
        from django.apps import apps
        guards = importlib.import_module("evaluations.migrations.0002_immutable_audit_guards")
        editor = connection.schema_editor()
        interactions = importlib.import_module("evaluations.migrations.0004_interaction_audit_guards")
        datasets = importlib.import_module("evaluations.migrations.0006_dataset_audit_guards")
        calibration = importlib.import_module("evaluations.migrations.0008_calibration_audit_guards")
        approval_supersessions = importlib.import_module("evaluations.migrations.0009_calibration_plan_and_approval_supersession")
        approval_supersessions.uninstall_guard(apps, editor)
        calibration.uninstall(apps, editor)
        datasets.uninstall(apps, editor)
        interactions.uninstall(apps, editor)
        guards.uninstall(apps, editor)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE evaluations_metricdefinition SET actor_label = %s WHERE id = %s", ["probe", self.structure.metric_id])
        guards.install(apps, editor)
        interactions.install(apps, editor)
        datasets.install(apps, editor)
        calibration.install(apps, editor)
        approval_supersessions.install_guard(apps, editor)
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM evaluations_metricdefinition WHERE id = %s", [self.structure.metric_id])

    def test_manifest_cannot_claim_another_run(self):
        result, _ = self.assess()
        manifest = copy.deepcopy(result.input_manifest)
        manifest["evaluated_run"] = "another-run"
        values = self.result_values(result)
        values.update(input_manifest=manifest, input_manifest_hash=canonical_hash(manifest), idempotency_key="cross-target")
        with self.assertRaises(ValidationError):
            record_result(**values)

    def test_fake_grader_rejected_for_deterministic_evaluation(self):
        result, _ = self.assess()
        values = self.result_values(result)
        values.update(grader_run=self.target_run, idempotency_key="fake-grader")
        with self.assertRaises(ValidationError):
            record_result(**values)

    def test_evaluator_identity_cannot_be_renamed_but_can_be_disabled(self):
        definition = self.structure.evaluator
        definition.key = "different.identity"
        with self.assertRaises(ValidationError):
            definition.save()
        with self.assertRaises(DatabaseError), transaction.atomic():
            EvaluatorDefinition.objects.filter(pk=definition.pk).update(key="different.identity")
        EvaluatorDefinition.objects.filter(pk=definition.pk).update(is_active=False)
        self.assertFalse(EvaluatorDefinition.objects.get(pk=definition.pk).is_active)


class DeterministicTests(TestCase):
    def manifest(self, contract=None, finish="stop"):
        return {"output_contract": contract or {"format": "text"}, "finish_reason": finish, "reference_observations": []}

    def statuses(self, output, manifest):
        return {r["id"]: r["status"] for r in evaluate(output, manifest)}

    def test_text_does_not_fail_json_schema(self):
        result = self.statuses(b"Concise decisive finding.", self.manifest())
        self.assertEqual(result["output.schema"], "not_applicable")
        self.assertEqual(result["output.nonempty"], "pass")

    def test_empty_and_truncated_are_distinct(self):
        result = self.statuses(b"  ", self.manifest(finish="length"))
        self.assertEqual(result["output.nonempty"], "fail")
        self.assertEqual(result["output.truncation"], "fail")
        self.assertEqual(self.statuses(b"Complete sentence.", self.manifest(finish=""))["output.truncation"], "insufficient_evidence")

    def test_json_type_required_and_duplicate_key_failures(self):
        contract = {"format": "json_object", "fields": {"answer": "string"}, "required": ["answer"]}
        for output in (b'[]', b'{"answer":3}', b'{"answer":"a","answer":"b"}', b'{"answer":NaN}'):
            with self.subTest(output=output):
                self.assertEqual(self.statuses(output, self.manifest(contract))["output.schema"], "fail")
        for output in (b'{}', b'{"answer":"  "}', b'{"answer":null}'):
            with self.subTest(output=output):
                self.assertEqual(self.statuses(output, self.manifest(contract))["output.required_fields"], "fail")

    def test_unsupported_schema_features_rejected(self):
        with self.assertRaises(ValidationError):
            evaluate(b'{}', self.manifest({"format": "json_object", "properties": {}}))
