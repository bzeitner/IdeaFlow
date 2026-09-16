import tempfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from evaluations.models import EvaluationResult
from evaluations.seeds import seed_evaluators
from evaluations.services import evaluate_run, record_result
from executions.models import ExecutionEvent, LLMRun
from executions.services import canonical_hash
from executions.storage import ExecutionPayloadStore
from . import test_review_regressions as fixtures
from .base import AuditTransactionTestCase
from .helpers import approved_excerpt


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"evaluators": True})
class EvidenceSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.progress, cls.quality, cls.structure = seed_evaluators()
        cls.target_run, cls.output = fixtures.make_completed_run()

    values = fixtures.EvidenceRegressionTests.values

    def assert_rejected(self, values):
        values["input_manifest_hash"] = canonical_hash(values["input_manifest"])
        with self.assertRaises(ValidationError):
            record_result(**values)
        self.assertFalse(EvaluationResult.objects.filter(idempotency_key="review-result").exists())

    def test_extra_fields_cannot_smuggle_output_or_other_content(self):
        for location in ("output", "manifest", "criterion"):
            with self.subTest(location=location):
                values = self.values(self.quality)
                target = {"output": values["input_manifest"]["evidence"]["output"],
                          "manifest": values["input_manifest"],
                          "criterion": values["criterion_results"][0]}[location]
                target["value"] = "Unapproved private content"
                self.assert_rejected(values)

    @override_settings(SECRET_KEY="dummy-configured-credential-123456789")
    def test_credentials_rejected_in_all_free_text_and_nested_evidence(self):
        secret = "dummy-configured-credential-123456789"
        for location in ("excerpt", "rationale", "reason", "actor", "output"):
            with self.subTest(location=location):
                values = self.values(self.quality)
                if location == "excerpt":
                    values["input_manifest"]["evidence"]["objective"] = approved_excerpt("objective", secret)
                elif location == "reason":
                    values["criterion_results"][0]["reason"] = secret
                elif location == "output":
                    values["input_manifest"]["evidence"]["output"]["value"] = secret
                else:
                    values["actor_label" if location == "actor" else "rationale"] = secret
                values["input_manifest_hash"] = canonical_hash(values["input_manifest"])
                with self.assertRaises(ValidationError) as raised:
                    record_result(**values)
                self.assertNotIn(secret, str(raised.exception))

    def test_credential_patterns_and_environment_secrets_are_rejected(self):
        from evaluations.security import validate_metadata
        examples = [{"authorization": "unrecognized-token"},
                    "Bearer dummy-token-123456789", "password=hunter-example",
                    "https://example.test/?X-Amz-Signature=dummy",
                    "https://user:dummy@example.test", "-----BEGIN PRIVATE KEY-----",
                    {"nested": ["fake-env-secret-123456789"]}]
        with patch.dict("os.environ", {"EXAMPLE_API_KEY": "fake-env-secret-123456789"}):
            for value in examples:
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    validate_metadata(value)
        validate_metadata({"authorization": "[REDACTED]"})

    def test_excerpt_approval_binds_operator_policy_and_exact_value(self):
        for field in (None, "policy", "approved_by", "value_hash"):
            values = self.values(self.quality)
            item = values["input_manifest"]["evidence"]["objective"]
            if field is None:
                item.pop("approval")
            else:
                item["approval"][field] = "different"
            with self.subTest(field=field):
                self.assert_rejected(values)

    def test_excerpt_and_total_metadata_limits(self):
        values = self.values(self.quality)
        values["input_manifest"]["evidence"]["objective"] = approved_excerpt("objective", "x" * 4097)
        self.assert_rejected(values)
        values = self.values(self.quality)
        values["rationale"] = "x" * 65537
        self.assert_rejected(values)

    def test_direct_model_create_cannot_bypass_secret_validation(self):
        values = self.values(self.quality)
        values["rationale"] = "Bearer dummy-token-123456789"
        with self.assertRaises(ValidationError):
            EvaluationResult.objects.create(**values)

    def test_view_only_admin_sees_diagnostics_but_no_excerpt_or_rationale(self):
        values = self.values(self.quality)
        excerpt = "Approved private observation withheld from general audit viewers"
        values["input_manifest"]["evidence"]["objective"] = approved_excerpt("objective", excerpt)
        values["input_manifest_hash"] = canonical_hash(values["input_manifest"])
        values["rationale"] = "Private overall rationale"
        values["criterion_results"][0]["reason"] = "Private criterion rationale"
        result, _ = record_result(**values)
        user = get_user_model().objects.create_user(username="audit-viewer")
        get_user_model().objects.filter(pk=user.pk).update(is_staff=True, is_superuser=False)
        user.refresh_from_db()
        user.user_permissions.add(Permission.objects.get(content_type__app_label="evaluations", codename="view_evaluationresult"))
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("admin:evaluations_evaluationresult_change", args=[result.pk]))
        self.assertEqual(response.status_code, 200)
        for hidden in (excerpt, values["rationale"], values["criterion_results"][0]["reason"]):
            self.assertNotContains(response, hidden)
        self.assertContains(response, result.output_hash)
        self.assertContains(response, "Criterion diagnostics")


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"evaluators": True})
class PayloadAuditSecurityTests(AuditTransactionTestCase):
    def setUp(self):
        self.progress, self.quality, self.structure = seed_evaluators()
        self.target_run, self.output = fixtures.make_completed_run()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        setting = override_settings(IDEAFLOW_EXECUTION_PAYLOAD_ROOT=self.tmp.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.store = ExecutionPayloadStore()
        self.capture(self.output)

    def capture(self, content):
        stored = self.store.put("output", content)
        LLMRun.objects.filter(pk=self.target_run.pk).update(output_ref=stored.reference, output_hash=canonical_hash(content))
        self.target_run.refresh_from_db()

    def assess(self):
        return evaluate_run(self.target_run, self.structure, actor_label="operator",
                            idempotency_key="stored", store=self.store)

    def events(self):
        return list(ExecutionEvent.objects.filter(run=self.target_run, event_type__startswith="payload.").order_by("sequence"))

    def assert_read_audited(self):
        events = self.events()
        self.assertEqual([e.event_type for e in events], ["payload.access_requested", "payload.accessed"])
        self.assertEqual(events[0].payload["access_id"], events[1].payload["access_id"])
        self.assertEqual(events[1].payload["actor_label"], "operator")
        return events

    def test_success_and_idempotent_retry_audit_only_actual_read(self):
        before = LLMRun.objects.values().get(pk=self.target_run.pk)
        result, created = self.assess()
        self.assertTrue(created)
        self.assertEqual(self.assess(), (result, False))
        self.assertTrue(self.assert_read_audited()[1].payload["hash_verified"])
        self.assertEqual(before, LLMRun.objects.values().get(pk=self.target_run.pk))

    def test_decode_failure_keeps_committed_access(self):
        self.capture(b"\xff")
        with self.assertRaises(ValidationError):
            self.assess()
        self.assert_read_audited()
        self.assertFalse(EvaluationResult.objects.exists())

    def test_result_transaction_failure_cannot_erase_access(self):
        with patch("evaluations.services.record_result", side_effect=ValidationError("Rejected result")):
            with self.assertRaises(ValidationError):
                self.assess()
        self.assert_read_audited()
        self.assertFalse(EvaluationResult.objects.exists())

    def test_hash_mismatch_is_still_audited(self):
        self.store = Mock(get=Mock(return_value=b"different bytes"))
        with self.assertRaises(ValidationError):
            self.assess()
        self.assertFalse(self.assert_read_audited()[1].payload["hash_verified"])

    def test_storage_failure_records_only_generic_failure(self):
        self.store = Mock(get=Mock(side_effect=OSError("private-storage-detail")))
        with self.assertRaises(OSError):
            self.assess()
        events = self.events()
        self.assertEqual([e.event_type for e in events], ["payload.access_requested", "payload.access_failed"])
        self.assertNotIn("private-storage-detail", str(events[-1].payload))

    def test_audit_failure_prevents_storage_read(self):
        self.store = Mock()
        with patch("evaluations.payloads.append_event", side_effect=DatabaseError("audit failed")):
            with self.assertRaises(DatabaseError):
                self.assess()
        self.store.get.assert_not_called()

    def test_application_outer_transaction_is_rejected_before_read(self):
        self.store = Mock()
        with transaction.atomic(), self.assertRaisesMessage(ValidationError, "another transaction"):
            self.assess()
        self.store.get.assert_not_called()
        self.assertEqual(self.events(), [])

    def test_manual_transaction_is_rejected_before_read(self):
        self.store = Mock()
        transaction.set_autocommit(False)
        try:
            with self.assertRaisesMessage(ValidationError, "autocommit"):
                self.assess()
        finally:
            connection.rollback()
            transaction.set_autocommit(True)
        self.store.get.assert_not_called()
        self.assertEqual(self.events(), [])

    def test_outcome_audit_failure_leaves_durable_attempt_and_no_result(self):
        from evaluations.payloads import append_event
        def fail_outcome(trace, event_type, **kwargs):
            if event_type == "payload.accessed":
                raise DatabaseError("outcome audit failed")
            return append_event(trace, event_type, **kwargs)
        with patch("evaluations.payloads.append_event", side_effect=fail_outcome):
            with self.assertRaises(DatabaseError):
                self.assess()
        self.assertEqual([e.event_type for e in self.events()], ["payload.access_requested"])
        self.assertFalse(EvaluationResult.objects.exists())
