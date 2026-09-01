import json
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase

from executions.models import CutoverMode, ServicePrincipal, WorkflowCutover


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
    def test_reconcile_emits_machine_readable_report(self):
        output = StringIO()
        call_command("phase4_reconcile", stdout=output)
        report = json.loads(output.getvalue())
        self.assertIn("provenance", report)
        self.assertIn("audit", report)
        self.assertIn("cutovers", report)

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
