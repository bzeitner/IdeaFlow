import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "score_queue.py"
SPEC = importlib.util.spec_from_file_location("score_queue", SCRIPT)
score_queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score_queue)


class ScoreQueueTests(TestCase):
    def test_no_qualifying_feeds_returns_empty_queue(self):
        idea = {
            "id": 54,
            "title": "An idea without highly rated feeds",
            "summary": "",
            "feeds": [{"id": 1, "rating": 3}],
        }

        output = io.StringIO()
        with patch.object(score_queue, "client", return_value=idea) as client:
            with redirect_stdout(output):
                score_queue.main(["54", "--min-rating", "4"])

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["queue_size"], 0)
        self.assertEqual(payload["returned"], 0)
        self.assertEqual(payload["items"], [])
        client.assert_called_once_with("dump-idea", "54")
