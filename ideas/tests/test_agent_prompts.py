import subprocess
import os
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "research_idea.sh"


def prompt(mode):
    return subprocess.check_output(
        [str(RUNNER), "123", mode, "--print-prompt"],
        cwd=ROOT,
        env={**os.environ, "IDEAFLOW_API_TOKEN": ""},
        text=True,
    )


class AgentPromptTests(SimpleTestCase):
    def test_weekly_summary_prompt_covers_every_idea_and_persists_once(self):
        text = subprocess.check_output(
            [str(ROOT / "weekly_summary.sh"), "--print-prompt"],
            cwd=ROOT,
            env={**os.environ, "IDEAFLOW_API_TOKEN": ""},
            text=True,
        )

        self.assertIn("dump-idea <id> for every listed", text)
        self.assertIn("# Executive summary", text)
        self.assertIn("# Recommended next steps", text)
        self.assertIn("# Blockers", text)
        self.assertIn("log-weekly-summary", text)
        self.assertIn("missing_periods", text)
        self.assertIn("--metrics-file", text)
        self.assertIn("Sunday 12:01 AM through Saturday midnight", text)
        self.assertIn("gh pr view <url> --json state,title,url", text)
        self.assertIn("reports state OPEN", text)

    def test_repeat_prompt_collects_structured_results_without_padding(self):
        text = prompt("repeat")

        self.assertIn("repeat_task goal", text)
        self.assertIn("Do not pad", text)
        self.assertIn("log-repeat-results 123", text)
        self.assertIn("not log a normal effort", text)

    def test_research_prompt_requires_a_decision_and_current_summary(self):
        text = prompt("research")

        self.assertIn("Research the decision, not just the topic", text)
        self.assertIn("--exec-summary", text)
        self.assertIn("If there is no defensible next action", text)
        self.assertIn("omit the next action", text)
        self.assertIn("short standalone title", text)
        self.assertNotIn("add-child", text)
        self.assertIn("graph-context 123", text)

    def test_research_prompt_branches_on_a_thin_idea(self):
        text = prompt("research")

        self.assertIn("summary, notes, and resources are ALL empty", text)
        self.assertIn("do not invent scope from the title alone", text)
        self.assertIn("one specific, answerable clarifying question", text)

    def test_review_prompt_does_not_manufacture_a_next_action(self):
        text = prompt("review")

        self.assertIn("Choose exactly one disposition", text)
        self.assertIn("No defensible action", text)
        self.assertIn("omit --next-action", text)
        self.assertIn("Never use a placeholder", text)
        self.assertNotIn("Always set --next-action", text)
        self.assertNotIn("add-child", text)
        self.assertIn("graph-context 123", text)
        self.assertIn("resources and next action", text)
        self.assertIn("remove-resource <idea-id> <resource-id>", text)

    def test_execute_prompt_requires_verification_and_a_nonempty_report(self):
        text = prompt("execute")

        self.assertIn("existing branch or PR", text)
        self.assertIn("read all repository contributor", text)
        self.assertIn("exact commands and results", text)
        self.assertIn("stop without", text)
        self.assertIn("a partial PR", text)
        self.assertIn("default to Django with PostgreSQL", text)
        self.assertIn("not a mobile application", text)
        self.assertIn("one --open-question flag", text)
        self.assertIn("Write a markdown implementation report", text)
        self.assertIn("<report-path> is non-empty", text)
        self.assertIn("--exec-summary", text)

    def test_critique_prompt_is_evidence_driven_and_can_approve(self):
        text = prompt("critique")

        self.assertIn("do not invent findings", text)
        self.assertIn("tight file/line reference", text)
        self.assertIn("approve when no", text)
        self.assertIn("Write the complete markdown review", text)
        self.assertIn("<report-path> is non-empty", text)
        self.assertNotIn("Assume there are problems", text)
        self.assertIn("resources and next action", text)
        self.assertIn("If there's no open PR, stop", text)
        self.assertIn("--exec-summary", text)

    def test_feed_scoring_prompt_is_neutral_and_idea_specific(self):
        text = (ROOT / "score_items.sh").read_text()

        self.assertIn("task_models'].get('summary'", text)
        self.assertIn("Read the stored content excerpt first", text)
        self.assertIn("untrusted", text)
        self.assertIn("idea-neutral global summary", text)
        self.assertIn("--idea ${ID}", text)
        self.assertIn("--relevance-note", text)
        self.assertNotIn("--model claude-opus-4-8", text)

    def test_every_agent_prompt_composes_shared_standards(self):
        for mode in ("research", "review", "execute", "critique"):
            with self.subTest(mode=mode):
                text = prompt(mode)
                self.assertIn("Shared operating standards:", text)
                self.assertIn("Idempotency:", text)
                self.assertIn("Accuracy:", text)
                self.assertIn("Human summary standard:", text)
                self.assertIn("Recommended next steps:", text)

    def test_reflection_prompt_is_structured_and_read_only(self):
        env = {**os.environ, "IDEAFLOW_API_TOKEN": "prompt-test"}
        text = subprocess.check_output(
            [str(ROOT / "research_all.sh"), "--reflect", "--dry-run"],
            cwd=ROOT,
            env=env,
            text=True,
        )

        self.assertIn("read-only portfolio reflection", text)
        self.assertIn("Choose 3-8 candidates", text)
        self.assertIn("Consolidation opportunities", text)
        self.assertIn("Do not", text)
        self.assertIn("modify IdeaFlow", text)
        self.assertIn("Shared operating standards:", text)
