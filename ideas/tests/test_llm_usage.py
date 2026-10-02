import json
import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from tools.llm_usage import parse_claude, parse_codex, parse_antigravity, parse_provider_error
from tools.llm_pricing import estimate_openai_cost_micros


ROOT = Path(__file__).resolve().parents[2]


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

    def executable(self, body):
        # Keep executable fixtures on the workspace filesystem; system temp
        # directories may be mounted noexec in CI.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=ROOT, prefix=".test-agent-"
        )
        handle.write("#!/bin/sh\n" + body)
        handle.close()
        os.chmod(handle.name, 0o700)
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

    def test_extracts_claude_cli_error(self):
        path = self.write({
            "is_error": True,
            "terminal_reason": "api_error",
            "result": "Not logged in · Please run /login",
        })

        self.assertEqual(
            parse_provider_error("claude", path),
            "Not logged in · Please run /login",
        )

    def test_extracts_codex_cli_error(self):
        path = self.write(json.dumps({
            "type": "turn.failed",
            "error": {"message": "Authentication failed"},
        }))

        self.assertEqual(
            parse_provider_error("codex", path),
            "Authentication failed",
        )

    def test_provider_error_ignores_non_object_json(self):
        path = self.write(["unexpected", "shape"])

        self.assertEqual(parse_provider_error("claude", path), "")

    def test_claude_preflight_explains_profile_login(self):
        fake = self.executable('printf \'{"loggedIn":false,"authMethod":"none"}\\n\'\nexit 1\n')
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            [
                "bash", "-c",
                'source "$1"; agent_require_ready claude "$2"',
                "test", str(preflight), fake,
            ],
            env={**os.environ, "CLAUDE_CONFIG_DIR": "/tmp/claude alt"},
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Claude Code is not logged in", completed.stderr)
        self.assertIn("CLAUDE_CONFIG_DIR=/tmp/claude alt", completed.stderr)
        self.assertIn("auth login", completed.stderr)

    def test_claude_preflight_accepts_logged_in_profile(self):
        fake = self.executable('printf \'notice\\n{"loggedIn":true,"authMethod":"oauthAccount"}\\n\'\n')
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            [
                "bash", "-c",
                'source "$1"; agent_require_ready claude "$2"',
                "test", str(preflight), fake,
            ],
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_claude_preflight_warns_and_continues_on_unknown_output(self):
        fake = self.executable("printf 'legacy status output\\n'\nexit 2\n")
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            ["bash", "-c", 'source "$1"; agent_require_ready claude "$2"',
             "test", str(preflight), fake],
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("status was not recognized", completed.stderr)

    def test_claude_preflight_warns_and_continues_on_timeout(self):
        fake = self.executable("sleep 2\n")
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            ["bash", "-c", 'source "$1"; agent_require_ready claude "$2"',
             "test", str(preflight), fake],
            env={**os.environ, "IDEAFLOW_AGENT_PREFLIGHT_TIMEOUT_SECONDS": "0.05"},
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("timed out", completed.stderr)

    def test_claude_preflight_accepts_api_key_auth_without_login_status(self):
        fake = self.executable("exit 99\n")
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            ["bash", "-c", 'source "$1"; agent_require_ready claude "$2"',
             "test", str(preflight), fake],
            env={**os.environ, "ANTHROPIC_API_KEY": "test-only-not-a-real-key"},
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_claude_preflight_accepts_third_party_provider_credentials(self):
        fake = self.executable("exit 99\n")
        preflight = ROOT / "tools" / "agent_preflight.sh"

        for variable, value in (
            ("CLAUDE_CODE_USE_BEDROCK", "TRUE"),
            ("CLAUDE_CODE_USE_VERTEX", "true"),
        ):
            with self.subTest(variable=variable):
                completed = subprocess.run(
                    ["bash", "-c", 'source "$1"; agent_require_ready claude "$2"',
                     "test", str(preflight), fake],
                    env={**os.environ, variable: value},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_preflight_identity_changes_with_profile(self):
        fake = self.executable("exit 0\n")
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            [
                "bash", "-c",
                'source "$1"; CLAUDE_CONFIG_DIR=one agent_preflight_identity claude "$2"; '
                'printf "\\n"; CLAUDE_CONFIG_DIR=two agent_preflight_identity claude "$2"',
                "test", str(preflight), fake,
            ],
            text=True,
            capture_output=True,
        )

        first, second = completed.stdout.splitlines()
        self.assertNotEqual(first, second)

    def test_antigravity_missing_binary_restores_install_hint(self):
        preflight = ROOT / "tools" / "agent_preflight.sh"

        completed = subprocess.run(
            ["bash", "-c", 'source "$1"; agent_require_ready antigravity "$2"',
             "test", str(preflight), "/missing/agy"],
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("https://antigravity.google/cli/install.sh", completed.stderr)

    def test_runner_snapshot_reuses_the_running_bash(self):
        for name in ("research_all.sh", "research_idea.sh"):
            with self.subTest(script=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn('exec "$BASH" -c', source)
                self.assertNotIn("exec /bin/bash", source)

    def test_runner_preflights_before_selection_and_claiming(self):
        batch_source = (ROOT / "research_all.sh").read_text(encoding="utf-8")
        child_source = (ROOT / "research_idea.sh").read_text(encoding="utf-8")

        self.assertLess(batch_source.index('agent_require_ready "$AGENT" "$AGENT_BIN"'),
                        batch_source.index('python3 "$SCRIPT_DIR/tools/select_tasks.py"'))
        self.assertLess(batch_source.index('agent_require_ready "$AGENT" "$AGENT_BIN"'),
                        batch_source.index('"$IFCLI" claim-job'))
        self.assertIn('if [[ "$DRY_RUN" -eq 0 ]]', batch_source)
        self.assertIn('agent_preflight_identity "$AGENT" "$AGENT_BIN"', batch_source)
        self.assertIn('"${IDEAFLOW_AGENT_PREFLIGHTED:-}" != "$PREFLIGHT_IDENTITY"', child_source)

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

    def test_parses_antigravity_aggregate_usage_and_provider_cost(self):
        path = self.write({
            "result": "done agy", "conversation_id": "conv-123", "total_cost_usd": 0.005,
            "usage": {
                "input_tokens": 50, "output_tokens": 20,
                "cache_read_tokens": 10,
                "thinking_tokens": 5,
                "total_tokens": 70,
            },
        })

        text, measurement = parse_antigravity(path)

        self.assertEqual(text, "done agy")
        self.assertEqual(measurement["total_tokens"], 70)
        self.assertEqual(measurement["cached_tokens"], 10)
        self.assertEqual(measurement["reasoning_tokens"], 5)
        self.assertEqual(measurement["cost_micros"], 5_000)
        self.assertEqual(measurement["cost_source"], "provider_reported")
        self.assertEqual(measurement["provider_request_id"], "conv-123")

    def test_prefers_antigravity_structured_output_and_allocated_cost(self):
        path = self.write({
            "result": "fallback text",
            "structured_output": {"status": "ok"},
            "conversation_id": "session-456",
            "usage": {"input_tokens": 15, "output_tokens": 10, "total_tokens": 25},
        })

        text, measurement = parse_antigravity(path, allocated_cost_micros=10_000)

        self.assertEqual(json.loads(text), {"status": "ok"})
        self.assertEqual(measurement["total_tokens"], 25)
        self.assertEqual(measurement["cost_micros"], 10_000)
        self.assertEqual(measurement["cost_source"], "subscription_allocated")
        self.assertEqual(measurement["provider_request_id"], "session-456")

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

    def test_execution_start_accepts_subjectless_workflow_under_nounset(self):
        fake = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        fake.write(
            '#!/bin/sh\n'
            'case "$1" in\n'
            '  trace-start) printf \'{"id":"trace-1"}\\n\' ;;\n'
            '  run-start) printf \'{"id":"run-1"}\\n\' ;;\n'
            '  *) exit 1 ;;\n'
            'esac\n'
        )
        fake.close()
        os.chmod(fake.name, 0o700)
        self.addCleanup(Path(fake.name).unlink, missing_ok=True)
        telemetry = Path(__file__).resolve().parents[2] / "tools" / "execution_telemetry.sh"

        completed = subprocess.run(
            [
                "bash", "-uc",
                'source "$1"; IFCLI="$2"; '
                'execution_start weekly_summary "" claude model generation "$3"',
                "test", str(telemetry), fake.name, fake.name,
            ],
            env={**os.environ, "IDEAFLOW_EXECUTION_API_TOKEN": "test-token"},
            text=True, capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("execution trace: trace-1; run: run-1", completed.stderr)

    def test_execution_succeed_accepts_complete_measurements_under_nounset(self):
        calls = self.write("")
        fake = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        fake.write('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS_FILE"\n')
        fake.close()
        os.chmod(fake.name, 0o700)
        self.addCleanup(Path(fake.name).unlink, missing_ok=True)
        output = self.write("completed output")
        measurement = self.write({
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cost_micros": 500,
            "cost_source": "provider_reported",
        })
        telemetry = Path(__file__).resolve().parents[2] / "tools" / "execution_telemetry.sh"

        completed = subprocess.run(
            [
                "bash", "-uc",
                'source "$1"; IFCLI="$2"; IDEAFLOW_TELEMETRY_ACTIVE=1; '
                'IDEAFLOW_RUN_ID=run-1; IDEAFLOW_TRACE_ID=trace-1; '
                'execution_succeed "$3" "$4"',
                "test", str(telemetry), fake.name, output, measurement,
            ],
            env={**os.environ, "CALLS_FILE": calls},
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        recorded = Path(calls).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(recorded), 2)
        self.assertIn("run-complete --run-id run-1", recorded[0])
        self.assertIn("--measurement-status complete", recorded[0])
        self.assertNotIn("--measurement-unavailable-reason", recorded[0])
        self.assertEqual(recorded[1], "trace-complete --trace-id trace-1")
