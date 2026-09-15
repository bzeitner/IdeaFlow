import hashlib
import json
import stat
import os
import runpy
from types import SimpleNamespace
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from executions.management.commands.phase4_reconcile import Command as Reconcile
from executions.models import ExecutionEvent, LLMRun, ServicePrincipal
from executions.storage import ExecutionPayloadStore, PayloadExpired
from executions.tests.test_api import FLAGS_ON
from ideas.graph.semantic import SemanticAPI


@override_settings(IDEAFLOW_EXECUTION_FLAGS=FLAGS_ON, IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS=True)
class PayloadCaptureTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "live"
        self.backup = Path(self.directory.name) / "backup"
        settings = override_settings(
            IDEAFLOW_EXECUTION_PAYLOAD_ROOT=self.root,
            IDEAFLOW_EXECUTION_PAYLOAD_BACKUP_ROOT=str(self.backup),
            IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS=30,
            IDEAFLOW_EXECUTION_CAPTURE_SINCE="",
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self.principal = ServicePrincipal.objects.create(
            name="capture-writer", token_hash=ServicePrincipal.hash_token("capture-writer-token"),
            scopes=["execution:write", "execution:read"],
        )
        self.headers = {"HTTP_AUTHORIZATION": "Bearer capture-writer-token"}
        trace = self.post("/api/executions/v1/traces/", {"workflow": "feed_score"})
        self.assertEqual(trace.status_code, 201, trace.content)
        self.trace_id = trace.json()["id"]

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", **self.headers)

    def start(self, content="Exact prompt — unicode included"):
        return self.post(f"/api/executions/v1/traces/{self.trace_id}/runs/", {
            "provider": "test", "model": "capture-test", "rendered_input": content,
            "rendered_input_hash": hashlib.sha256(content.encode()).hexdigest(),
        })

    def test_capture_exact_bytes_scoped_retrieval_and_access_audit(self):
        started = self.start()
        self.assertEqual(started.status_code, 201, started.content)
        run_id = started.json()["id"]
        content = "Exact raw response."
        response = self.post(f"/api/executions/v1/runs/{run_id}/complete/", {
            "output": content, "output_hash": hashlib.sha256(content.encode()).hexdigest(),
            "measurement_status": "unavailable", "measurement_unavailable_reasons": ["synthetic_test"],
        })
        self.assertEqual(response.status_code, 200, response.content)
        run = LLMRun.objects.get(pk=run_id)
        store = ExecutionPayloadStore()
        self.assertTrue(store.verify(run.rendered_input_ref, run.rendered_input_hash))
        self.assertTrue(store.verify(run.output_ref, run.output_hash))
        path = f"/api/executions/v1/runs/{run_id}/payloads/output/"
        self.assertEqual(self.client.get(path, **self.headers).status_code, 403)
        self.principal.scopes.append("execution:payload:read")
        self.principal.save()
        fetched = self.client.get(path, **self.headers)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.content, content.encode())
        self.assertEqual(fetched["Cache-Control"], "no-store")
        event = ExecutionEvent.objects.get(event_type="payload.accessed")
        self.assertEqual(event.payload["principal_id"], self.principal.pk)
        self.assertNotIn(content, json.dumps(event.payload))
        for path in self.root.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_missing_content_and_mismatched_hash_do_not_create_payload_files(self):
        path = f"/api/executions/v1/traces/{self.trace_id}/runs/"
        base = {"provider": "test", "model": "capture-test", "rendered_input_hash": "a" * 64}
        self.assertEqual(self.post(path, base).status_code, 400)
        self.assertEqual(self.post(path, {**base, "rendered_input": "wrong hash"}).status_code, 400)
        self.assertEqual(list(self.root.rglob("*.payload")), [])

    def test_credentials_are_rejected_without_writing_or_echoing_them(self):
        secret = "do-not-store-this-credential"
        with override_settings(IDEAFLOW_API_TOKEN=secret):
            response = self.start(f"Use {secret}")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(secret.encode(), response.content)
        with self.assertRaises(ValidationError):
            ExecutionPayloadStore().put("prompt", "-----BEGIN PRIVATE KEY-----\nprivate")
        self.assertEqual(list(self.root.rglob("*.payload")), [])

    def test_retention_backup_restore_and_expiry_tombstones(self):
        store = ExecutionPayloadStore()
        recent = store.put("prompt", "retained")
        with patch("executions.storage.timezone.now", return_value=timezone.now() - timedelta(days=31)):
            old = store.put("response", "expired")
        with self.assertRaises(PayloadExpired):
            store.get(old.reference)
        output = StringIO()
        call_command("maintain_execution_payloads", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["expired"], 1)
        self.assertEqual(len(list(self.root.rglob("*.payload"))), 2)
        output = StringIO()
        call_command("maintain_execution_payloads", "--apply", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["backup_restored_verified"], 1)
        self.assertEqual(len(list(self.root.rglob("*.payload"))), 1)
        self.assertEqual(len(list(self.backup.rglob("*.payload"))), 1)
        self.assertEqual(store.get(recent.reference), b"retained")
        with self.assertRaises(PayloadExpired):
            store.get(old.reference)
        with patch("executions.management.commands.maintain_execution_payloads.timezone.now",
                   return_value=timezone.now() + timedelta(days=31)):
            call_command("maintain_execution_payloads", "--apply", stdout=StringIO())
        self.assertEqual(list(self.root.rglob("*.payload")), [])
        self.assertEqual(list(self.backup.rglob("*.payload")), [])

    def test_backup_rejects_corruption_and_overlapping_or_public_roots(self):
        stored = ExecutionPayloadStore().put("prompt", "original")
        filename = self.root / stored.reference.removeprefix("execution://")
        filename.write_bytes(b"corrupt")
        with self.assertRaisesMessage(CommandError, "checksum mismatch"):
            call_command("maintain_execution_payloads", "--apply")
        with override_settings(IDEAFLOW_EXECUTION_PAYLOAD_BACKUP_ROOT=str(self.root / "backup")):
            with self.assertRaisesMessage(CommandError, "must not overlap"):
                call_command("maintain_execution_payloads", "--apply")
        with override_settings(MEDIA_ROOT=self.root):
            with self.assertRaisesMessage(CommandError, "outside MEDIA_ROOT"):
                call_command("maintain_execution_payloads", "--apply")

    def test_reconciliation_distinguishes_pre_capture_missing_and_expired(self):
        self.assertEqual(self.start().status_code, 201)
        run = LLMRun.objects.get()
        LLMRun.objects.filter(pk=run.pk).update(rendered_input_ref="")
        with override_settings(IDEAFLOW_EXECUTION_CAPTURE_SINCE=(timezone.now() + timedelta(seconds=1)).isoformat()):
            report = Reconcile._payload_report(LLMRun.objects.all())
        self.assertTrue(report["healthy"])
        self.assertEqual(report["before_capture_enabled"], 1)
        self.assertFalse(Reconcile._payload_report(LLMRun.objects.all())["healthy"])
        LLMRun.objects.filter(pk=run.pk).update(rendered_input_ref=run.rendered_input_ref)
        with patch("executions.storage.timezone.now", return_value=timezone.now() + timedelta(days=31)):
            report = Reconcile._payload_report(LLMRun.objects.all())
        self.assertTrue(report["healthy"])
        self.assertEqual(report["expired"], 1)

    def test_semantic_provider_captures_matching_request_and_response(self):
        api = SemanticAPI(api_key="synthetic-key")
        payload = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Test"}]}
        response = {"id": "test-response", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        with patch.object(api, "_post", return_value=response):
            _, run = api._measured_post("/chat/completions", payload, purpose="classification")
        run.refresh_from_db()
        store = ExecutionPayloadStore()
        self.assertTrue(store.verify(run.rendered_input_ref, run.rendered_input_hash))
        self.assertTrue(store.verify(run.output_ref, run.output_hash))
        self.assertEqual(json.loads(store.get(run.rendered_input_ref)), payload)
        self.assertEqual(json.loads(store.get(run.output_ref)), response)

    def test_cli_reads_capture_policy_each_invocation_and_honors_env_override(self):
        cli = runpy.run_path(str(Path(__file__).resolve().parents[2] / "tools" / "ideaflow"))
        enabled = cli["_capture_payloads_enabled"]
        config = Path(self.directory.name) / ".ideaflow" / "client.json"
        config.parent.mkdir()
        with patch("pathlib.Path.home", return_value=Path(self.directory.name)), patch.dict(os.environ):
            os.environ.pop("IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS", None)
            self.assertFalse(enabled())
            config.write_text('{"capture_payloads": true}')
            self.assertTrue(enabled())
            os.environ["IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS"] = "false"
            self.assertFalse(enabled())

    def test_large_payload_and_api_envelope_limit(self):
        self.assertEqual(self.start("x" * (3 * 1024 * 1024)).status_code, 201)
        with override_settings(IDEAFLOW_EXECUTION_API_MAX_BYTES=64):
            self.assertEqual(self.start("over the envelope limit").status_code, 400)

    def test_cli_payload_preserves_crlf_bytes_and_hash(self):
        cli = runpy.run_path(str(Path(__file__).resolve().parents[2] / "tools" / "ideaflow"))
        path = Path(self.directory.name) / "prompt.txt"
        path.write_bytes(b"first\r\nsecond\r\n")
        start = cli["cmd_run_start"]
        args = SimpleNamespace(provider="test", model="test", purpose="generation",
                               prompt_key=[], input_file=str(path), idempotency_key="test", trace_id="test")
        with patch.dict(os.environ, {"IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS": "true"}), patch.dict(
            start.__globals__, {"_request": lambda *a, **kw: kw["body"]}
        ):
            body = start(args)
        self.assertEqual(body["rendered_input"].encode(), path.read_bytes())
        self.assertEqual(body["rendered_input_hash"], hashlib.sha256(path.read_bytes()).hexdigest())
