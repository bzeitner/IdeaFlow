import json
import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from tools.llm_usage import parse_claude, parse_codex
from tools.llm_pricing import estimate_openai_cost_micros


class LLMUsageParsingTests(SimpleTestCase):
    def write(self, value):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        if isinstance(value, str):
            handle.write(value)
        else:
            json.dump(value, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return handle.name

    def test_parses_claude_aggregate_usage_and_provider_cost(self):
        path = self.write({
            "result": "done", "session_id": "session-1", "total_cost_usd": 0.012345,
            "usage": {
                "input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30,
                "output_tokens_details": {"thinking_tokens": 2},
            },
        })

        text, measurement = parse_claude(path)

        self.assertEqual(text, "done")
        self.assertEqual(measurement["total_tokens"], 65)
        self.assertEqual(measurement["cached_tokens"], 50)
        self.assertEqual(measurement["reasoning_tokens"], 2)
        self.assertEqual(measurement["cost_micros"], 12_345)
        self.assertEqual(measurement["cost_source"], "provider_reported")

    def test_prefers_claude_structured_output(self):
        path = self.write({
            "result": "fallback text",
            "structured_output": {
                "decision": "accept",
                "rationale": "Supported.",
            },
            "usage": {},
        })

        text, _measurement = parse_claude(path)

        self.assertEqual(json.loads(text)["decision"], "accept")

    def test_parses_codex_jsonl_and_records_subscription_cost(self):
        path = self.write("\n".join((
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 20,
            }}),
        )))

        text, measurement = parse_codex(path, allocated_cost_micros=250_000)

        self.assertEqual(text, "done")
        self.assertEqual(measurement["total_tokens"], 120)
        self.assertEqual(measurement["cached_tokens"], 60)
        self.assertEqual(measurement["cost_micros"], 250_000)
        self.assertEqual(measurement["cost_source"], "subscription_allocated")

    def test_unknown_openai_model_is_rejected_before_use(self):
        with self.assertRaisesRegex(ValueError, "No approved pricing policy"):
            estimate_openai_cost_micros("unpriced-model", {})

    def test_execution_start_fails_closed_when_trace_registration_fails(self):
        fake = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        fake.write("#!/bin/sh\nexit 1\n")
        fake.close()
        os.chmod(fake.name, 0o700)
        self.addCleanup(Path(fake.name).unlink, missing_ok=True)
        telemetry = Path(__file__).resolve().parents[2] / "tools" / "execution_telemetry.sh"
        completed = subprocess.run(
            ["bash", "-c", 'source "$1"; IFCLI="$2"; execution_start research 1 claude model generation "$3"',
             "test", str(telemetry), fake.name, fake.name],
            env={**os.environ, "IDEAFLOW_EXECUTION_API_TOKEN": "test-token"},
            text=True, capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("trace registration failed", completed.stderr)
