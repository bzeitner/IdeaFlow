import subprocess
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "research_idea.sh"


def prompt(mode):
    return subprocess.check_output(
        [str(RUNNER), "123", mode, "--print-prompt"],
        cwd=ROOT,
        text=True,
    )


class AgentPromptTests(SimpleTestCase):
    def test_research_prompt_requires_a_decision_and_current_summary(self):
        text = prompt("research")

        self.assertIn("Research the decision, not just the topic", text)
        self.assertIn("--exec-summary", text)
        self.assertIn("If there is no defensible next action", text)
        self.assertIn("omit the next action", text)
        self.assertIn("short standalone title", text)
        self.assertNotIn("add-child", text)

    def test_review_prompt_does_not_manufacture_a_next_action(self):
        text = prompt("review")

        self.assertIn("Choose exactly one disposition", text)
        self.assertIn("No defensible action", text)
        self.assertIn("omit --next-action", text)
        self.assertIn("Never use a placeholder", text)
        self.assertNotIn("Always set --next-action", text)
        self.assertNotIn("add-child", text)

    def test_execute_prompt_requires_verification_and_a_nonempty_report(self):
        text = prompt("execute")

        self.assertIn("existing branch or PR", text)
        self.assertIn("read all repository contributor", text)
        self.assertIn("exact commands and results", text)
        self.assertIn("stop without opening", text)
        self.assertIn("a partial PR", text)
        self.assertIn("Write a markdown implementation report", text)
        self.assertIn("<report-path> is non-empty", text)

    def test_critique_prompt_is_evidence_driven_and_can_approve(self):
        text = prompt("critique")

        self.assertIn("do not invent findings", text)
        self.assertIn("tight file/line reference", text)
        self.assertIn("approve when no", text)
        self.assertIn("Write the complete markdown review", text)
        self.assertIn("<report-path> is non-empty", text)
        self.assertNotIn("Assume there are problems", text)
