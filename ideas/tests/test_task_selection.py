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
