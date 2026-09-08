from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from tools import review_relationships


class RelationshipCouncilJobTests(SimpleTestCase):
    def test_provider_plan_runs_both_claude_and_codex_across_three_personas(self):
        self.assertEqual(len(review_relationships.PROVIDERS), 3)
        self.assertEqual(set(review_relationships.PROVIDERS), {"claude", "codex"})

    def test_vote_parser_is_strict_and_accepts_valid_json(self):
        vote = review_relationships.parse_vote(
            '{"decision":"abstain","rationale":"Insufficient evidence."}'
        )
        self.assertEqual(vote["decision"], "abstain")

        with self.assertRaises(ValueError):
            review_relationships.parse_vote(
                '{"decision":"accept","rationale":""}'
            )

    def test_vote_parser_compacts_and_bounds_rationale(self):
        rationale = "first line\n" + ("word " * 100)
        vote = review_relationships.parse_vote(
            '{"decision":"accept","rationale":' + repr(rationale).replace("'", '"') + "}"
        )
        self.assertNotIn("\n", vote["rationale"])
        self.assertLessEqual(
            len(vote["rationale"]), review_relationships.RATIONALE_MAX_CHARS
        )

    def test_vote_schema_enforces_short_rationale(self):
        rationale = review_relationships.VOTE_SCHEMA["properties"]["rationale"]
        self.assertEqual(
            rationale["maxLength"], review_relationships.RATIONALE_MAX_CHARS
        )

    def test_prompt_requires_independent_evidence_based_vote(self):
        prompt = review_relationships.prompt_for(
            {
                "suggestion_id": 1,
                "source": {"title": "A"},
                "target": {"title": "B"},
                "relationship": {"type": "supports", "evidence": "Measured result"},
                "personas": [],
            },
            {"name": "Risk", "goals": "Safety", "constraints": "No guessing"},
        )
        self.assertIn("independently", prompt.lower())
        self.assertIn("untrusted", prompt)
        self.assertIn("accept|reject|abstain", prompt)
        self.assertIn("one evidence-based sentence", prompt)

    @patch("tools.review_relationships.parse_claude")
    @patch("tools.review_relationships.subprocess.check_output")
    def test_claude_vote_runs_outside_repository(self, check_output, parse_claude):
        check_output.return_value = "provider output"
        parse_claude.return_value = (
            '{"decision":"accept","rationale":"Supported."}',
            {"total_tokens": 10, "cost_micros": 1},
        )

        review_relationships.run_vote("claude", "prompt", "model")

        workdir = check_output.call_args.kwargs["cwd"]
        command = check_output.call_args.args[0]
        self.assertIn("--json-schema", command)
        self.assertNotEqual(workdir, str(review_relationships.ROOT))
        self.assertIn("ideaflow-relationship-vote-", workdir)

    @patch("tools.review_relationships.parse_codex")
    @patch("tools.review_relationships.subprocess.run")
    def test_codex_vote_runs_outside_repository_and_skips_git_check(
        self, run, parse_codex
    ):
        def complete(command, **kwargs):
            output_path = command[command.index("--output-last-message") + 1]
            Path(output_path).write_text(
                '{"decision":"accept","rationale":"Supported."}', encoding="utf-8"
            )
            return SimpleNamespace(stdout="provider output")

        run.side_effect = complete
        parse_codex.return_value = (
            '{"decision":"accept","rationale":"Supported."}',
            {"total_tokens": 10, "cost_micros": 1},
        )

        review_relationships.run_vote("codex", "prompt", "model")

        command = run.call_args.args[0]
        workdir = run.call_args.kwargs["cwd"]
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("--output-schema", command)
        self.assertNotEqual(workdir, str(review_relationships.ROOT))
        self.assertIn("ideaflow-relationship-vote-", workdir)

    @patch("tools.review_relationships.subprocess.check_output")
    def test_provider_capability_check_accepts_required_flags(self, check_output):
        check_output.side_effect = [
            "usage: claude --json-schema",
            "usage: codex exec --skip-git-repo-check --output-schema",
        ]

        review_relationships.validate_provider_capabilities()

        self.assertEqual(check_output.call_count, 2)

    @patch("tools.review_relationships.subprocess.check_output")
    def test_provider_capability_check_rejects_incompatible_codex(self, check_output):
        check_output.side_effect = [
            "usage: claude --json-schema",
            "usage: codex exec --output-schema",
        ]

        with self.assertRaisesRegex(RuntimeError, "--skip-git-repo-check"):
            review_relationships.validate_provider_capabilities()
