import json
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from executions.management.commands import phase4_test_rollback as rollback
from executions.models import CutoverMode, WorkflowCutover
from executions.services import start_run, start_trace
from executions.tests.helpers import make_configuration, make_workflow_version
from ideas.models import Category, Idea, WeeklySummary


@override_settings(IDEAFLOW_API_TOKEN="rollback-test-token")
class Phase4RollbackTests(TestCase):
    def setUp(self):
        for i, key in enumerate(rollback.PHASE4_WORKFLOWS):
            WorkflowCutover.objects.update_or_create(
                workflow_key=key,
                defaults={"mode": CutoverMode.values[i % len(CutoverMode.values)],
                          "reason": "Existing operator decision"},
            )
        # Verify preservation of existing business records and execution history.
        self.idea = Idea.objects.create(
            title="Existing idea", category=Category.objects.create(name="Existing")
        )
        self.trace, _ = start_trace(
            make_workflow_version("critique"), trigger="human", subject=self.idea
        )
        self.run, _ = start_run(
            self.trace, make_configuration(), purpose="generation", rendered_input_hash="a" * 64
        )
        WeeklySummary.objects.create(
            period_start="9990-01-01", period_end="9990-01-01",
            title="Preserve this summary", content="Existing content",
        )

    def snapshot(self):
        # Include ancillary records and immutable history, not only the primary
        # projection. Sequence counters may advance on PostgreSQL rollback.
        return {
            model._meta.label: list(model._base_manager.order_by("pk").values())
            for model in apps.get_models()
            if model._meta.app_label in {"ideas", "executions"}
        }

    def test_all_workflows_emit_real_probe_results_and_preserve_database(self):
        before = self.snapshot()
        started = timezone.now()
        output = StringIO()
        call_command("phase4_test_rollback", "--owner", "operator", stdout=output)
        evidence = json.loads(output.getvalue())
        self.assertEqual(set(evidence), set(rollback.PHASE4_WORKFLOWS))
        for key, entry in evidence.items():
            with self.subTest(workflow=key):
                self.assertEqual(entry["result"], "pass")
                self.assertEqual(entry["original_mode"], entry["restored_mode"])
                self.assertGreaterEqual(entry["tested_at"], started.isoformat())
                self.assertEqual([step["http_status"] for step in entry["steps"]],
                                 [409, 200 if key in {"execute", "critique"} else 201,
                                  409, 200 if key in {"execute", "critique"} else 201,
                                  409, 409])
                self.assertEqual([step["projection_count"] for step in entry["steps"]],
                                 [0, 1, 0, 1, 0, 0])
        self.assertEqual(self.snapshot(), before)

    def test_failure_after_successful_write_rolls_back_and_emits_no_evidence(self):
        before = self.snapshot()
        original_resolve = rollback.resolve

        def failing_resolve(path):
            match = original_resolve(path)
            original_view = match.func

            def fail_after_write(*args, **kwargs):
                response = original_view(*args, **kwargs)
                if response.status_code == 201:
                    raise CommandError("Synthetic failure after write")
                return response

            match.func = fail_after_write
            return match

        output = StringIO()
        with patch.object(rollback, "resolve", side_effect=failing_resolve):
            with self.assertRaisesMessage(CommandError, "Synthetic failure after write"):
                call_command("phase4_test_rollback", "--owner", "operator", stdout=output)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(self.snapshot(), before)

    def test_disabled_api_or_blank_owner_cannot_produce_evidence(self):
        with self.assertRaisesMessage(CommandError, "must not be blank"):
            call_command("phase4_test_rollback", "--owner", " ")
        with override_settings(IDEAFLOW_API_TOKEN=""):
            with self.assertRaisesMessage(CommandError, "must be configured"):
                call_command("phase4_test_rollback", "--owner", "operator")

    def test_missing_cutover_is_not_silently_created(self):
        WorkflowCutover.objects.filter(workflow_key="critique").delete()
        with self.assertRaisesMessage(CommandError, "Missing cutover records: critique"):
            call_command("phase4_test_rollback", "--owner", "operator")
        self.assertFalse(WorkflowCutover.objects.filter(workflow_key="critique").exists())

    def test_pr_workflow_selector_validates_identity_and_defaults_to_execute(self):
        path = f"/api/ideas/{self.idea.pk}/reconcile-pr/"
        base = {"url": "https://github.com/example/repo/pull/1", "state": "CLOSED"}
        WorkflowCutover.objects.filter(workflow_key="execute").update(mode="authoritative")
        WorkflowCutover.objects.filter(workflow_key="critique").update(mode="legacy")

        def post(payload):
            return self.client.post(
                path, data=json.dumps(payload), content_type="application/json",
                HTTP_AUTHORIZATION="Bearer rollback-test-token",
            )

        self.assertEqual(post(base).status_code, 409)
        self.assertEqual(post({**base, "workflow": "unknown"}).status_code, 400)
        self.assertEqual(post({**base, "workflow": "execute",
                               "execution_run_id": str(self.run.pk)}).status_code, 409)
        self.assertEqual(post({**base, "workflow": "critique"}).status_code, 200)
