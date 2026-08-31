from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from executions.models import ServicePrincipal


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
