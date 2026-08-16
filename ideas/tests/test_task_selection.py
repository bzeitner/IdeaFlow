import io
import json
import os
import sys
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from tools import select_tasks


class TaskSelectionStateTests(SimpleTestCase):
    def run_selector(self, listing, details):
        def fake_check_output(command):
            if command[1] == "list-ideas":
                payload = {"ideas": listing}
            else:
                payload = details[int(command[2])]
            return json.dumps(payload).encode()

        with tempfile.NamedTemporaryFile() as state_file:
            env = {
                "IF_STATE_FILE": state_file.name,
                "IF_FORCE": "0",
                "IF_REVIEW": "0",
                "IF_STATUS": "",
            }
            with (
                mock.patch.object(sys, "argv", ["select_tasks.py", "fake-client"]),
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch.object(
                    select_tasks.subprocess,
                    "check_output",
                    side_effect=fake_check_output,
                ),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                select_tasks.main()
            state_file.seek(0)
            return stdout.getvalue(), json.load(state_file)

    def test_distinguishes_idle_paused_and_archived_ideas(self):
        listing = [
            {"id": 1, "status": "tracking"},
            {"id": 2, "status": "tracking"},
            {"id": 3, "status": "archived"},
        ]
        details = {
            1: {"is_paused": False, "research_entries": [{"id": 1}], "next_action": ""},
            2: {"is_paused": True, "research_entries": [], "next_action": ""},
            3: {"is_paused": False, "research_entries": [], "next_action": ""},
        }

        output, state = self.run_selector(listing, details)

        self.assertEqual(output, "")
        self.assertEqual(state["reason"], "idle")
        self.assertEqual(state["idle_ids"], [1])
        self.assertEqual(state["paused_ids"], [2])
        self.assertEqual(state["archived_ids"], [3])

    def test_reports_when_no_ideas_match(self):
        output, state = self.run_selector([], {})

        self.assertEqual(output, "")
        self.assertEqual(state["reason"], "no_ideas")

    def test_due_repeat_task_is_selected_even_when_paused(self):
        listing = [{"id": 7, "status": "tracking", "title": "Daily leads"}]
        details = {
            7: {
                "is_paused": True,
                "title": "Daily leads",
                "research_entries": [],
                "next_action": "",
                "repeat_task": {"enabled": True, "is_due": True},
            }
        }

        output, state = self.run_selector(listing, details)

        self.assertIn("7 repeat Daily leads", output)
        self.assertEqual(state["reason"], "actionable")

    def test_manually_paused_repeat_task_is_not_selected(self):
        listing = [{"id": 8, "status": "tracking", "title": "Paused repeat"}]
        details = {
            8: {
                "title": "Paused repeat", "is_paused": False,
                "research_entries": [{"id": 1}], "next_action": "",
                "repeat_task": {"enabled": True, "paused": True, "is_due": False},
            }
        }
        output, state = self.run_selector(listing, details)
        self.assertEqual(output, "")
        self.assertEqual(state["reason"], "idle")

    def test_repo_backed_build_action_is_selected_for_execution(self):
        listing = [{"id": 9, "status": "tracking", "title": "Build it"}]
        details = {
            9: {
                "title": "Build it", "is_paused": False,
                "research_entries": [{"id": 1}],
                "next_action": "Implement the account dashboard and verify login works",
                "repo": "owner/project", "resources": [],
            }
        }

        output, _state = self.run_selector(listing, details)

        self.assertIn("9 execute Build it", output)

    def test_repo_backed_non_build_action_stays_in_review(self):
        listing = [{"id": 10, "status": "tracking", "title": "Validate it"}]
        details = {
            10: {
                "title": "Validate it", "is_paused": False,
                "research_entries": [{"id": 1}],
                "next_action": "Interview five customers and record their answers",
                "repo": "owner/project", "resources": [],
            }
        }

        output, _state = self.run_selector(listing, details)

        self.assertIn("10 review Validate it", output)
