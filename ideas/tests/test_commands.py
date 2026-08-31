import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ideas.models import ResearchEntry, Status
from ideas.instrumentation import CALL_SITES, METRICS, validate_registries

from .helpers import make_idea, make_podcast_show, make_stage


def run(command, *args):
    """Call a management command, returning its stdout as text."""
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


class DumpIdeaTests(TestCase):
    def test_dump_single_idea_as_json(self):
        idea = make_idea(title="Solo", notes="the notes")
        idea.resources.create(label="Docs", url="https://example.com")
        data = json.loads(run("dump_idea", str(idea.pk)))
        self.assertEqual(data["title"], "Solo")
        self.assertEqual(data["notes"], "the notes")
        self.assertEqual(data["resources"][0]["url"], "https://example.com")

    def test_dump_list_of_ideas(self):
        make_idea(title="A", status=Status.CURRENT)
        make_idea(title="B", status=Status.TRACKING)
        data = json.loads(run("dump_idea"))
        self.assertEqual({i["title"] for i in data["ideas"]}, {"A", "B"})

    def test_dump_podcast_includes_server_computed_minimum_words(self):
        idea = make_idea(title="Podcast")
        make_podcast_show(idea=idea, target_episode_duration_seconds=9)
        data = json.loads(run("dump_idea", str(idea.pk)))
        self.assertEqual(data["podcast_show"]["minimum_script_word_count"], 12)

    def test_dump_list_filtered_by_status(self):
        make_idea(title="A", status=Status.CURRENT)
        make_idea(title="B", status=Status.TRACKING)
        data = json.loads(run("dump_idea", "--status", "tracking"))
        self.assertEqual([i["title"] for i in data["ideas"]], ["B"])

    def test_unknown_idea_raises(self):
        with self.assertRaises(CommandError):
            run("dump_idea", "999999")


class AuditLlmBaselineTests(TestCase):
    def test_registry_is_valid_and_contains_stable_core_workflows(self):
        self.assertEqual(validate_registries(), [])
        self.assertIn("feed-score", {item.key for item in CALL_SITES})
        self.assertIn("research.accepted_7d", {item.key for item in METRICS})

    def test_registry_command_emits_versioned_json(self):
        data = json.loads(run("audit_llm_baseline", "--registry"))
        self.assertEqual(data["version"], "1.0.0")
        self.assertTrue(data["call_sites"])
        self.assertTrue(data["metrics"])

    def test_baseline_reports_current_database_without_mutation(self):
        idea = make_idea()
        before = idea.updated_at
        data = json.loads(run("audit_llm_baseline"))
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["ideas"]["total"], 1)
        self.assertIn("measurement_limitations", data)
        idea.refresh_from_db()
        self.assertEqual(idea.updated_at, before)

    def test_output_does_not_overwrite_existing_report(self):
        path = self._write_tmp("existing")
        with self.assertRaises(CommandError):
            run("audit_llm_baseline", "--registry", "--output", path)

    def _write_tmp(self, text):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".json")
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(lambda: __import__("os").remove(path))
        return path


class LogEffortTests(TestCase):
    def test_records_entry_with_result_link_and_moves(self):
        idea = make_idea(status=Status.CURRENT)
        stage = make_stage(name="Prototyping")
        out = run(
            "log_effort",
            str(idea.pk),
            "--topic", "Built a spike",
            "--model", "claude-opus-4-8",
            "--context", "Details here.",
            "--effort", "4",
            "--quality", "5",
            "--tokens", "9000",
            "--repo-url", "https://github.com/x/y",
            "--repo-label", "Repo",
            "--stage", stage.slug,
            "--status", "tracking",
        )
        json.loads(out)  # command prints valid JSON
        entry = ResearchEntry.objects.get()
        self.assertEqual(entry.topic, "Built a spike")
        self.assertEqual(entry.tokens_used, 9000)
        self.assertEqual(entry.model.slug, "claude-opus-4-8")
        self.assertEqual(idea.resources.get().url, "https://github.com/x/y")
        idea.refresh_from_db()
        self.assertEqual(idea.stage, stage)
        self.assertEqual(idea.status, Status.TRACKING)

    def test_context_file_overrides_context(self, ):
        idea = make_idea()
        path = self._write_tmp("A long report body.")
        run(
            "log_effort",
            str(idea.pk),
            "--topic", "t",
            "--model", "other",
            "--context", "ignored",
            "--context-file", path,
        )
        self.assertEqual(ResearchEntry.objects.get().context, "A long report body.")

    def test_unknown_model_raises(self):
        idea = make_idea()
        with self.assertRaises(CommandError):
            run("log_effort", str(idea.pk), "--topic", "t", "--model", "nope")
        self.assertFalse(ResearchEntry.objects.exists())

    def test_unknown_idea_raises(self):
        with self.assertRaises(CommandError):
            run("log_effort", "999999", "--topic", "t")

    def _write_tmp(self, text):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".md")
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(lambda: __import__("os").remove(path))
        return path


class ExtractOpenQuestionsTests(TestCase):
    def test_dry_run_then_backfills_markdown_questions(self):
        from ideas.reporting import record_effort

        idea = make_idea()
        entry, _resource = record_effort(
            idea,
            topic="Historical report",
            model="other",
            context="## Open Questions\n- Which market should we choose?\n\n## Risks\nLow.",
        )
        entry.open_questions = []
        entry.save(update_fields=["open_questions"])

        preview = run("extract_open_questions", "--dry-run")
        entry.refresh_from_db()
        self.assertIn("Which market should we choose?", preview)
        self.assertEqual(entry.open_questions, [])

        result = run("extract_open_questions")
        entry.refresh_from_db()
        self.assertIn("updated 1 entry", result)
        self.assertEqual(entry.open_questions, ["Which market should we choose?"])

    @override_settings(IDEAFLOW_SEMANTIC_API_KEY="test-key")
    @patch("ideas.management.commands.extract_open_questions.SemanticAPI._post")
    def test_optional_ai_extracts_only_high_confidence_questions(self, post):
        from ideas.reporting import record_effort

        idea = make_idea()
        entry, _resource = record_effort(
            idea,
            topic="Unstructured report",
            model="other",
            context="We still need the owner to decide whether the budget is acceptable.",
        )
        post.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "questions": [
                                    {"question": "Is the proposed budget acceptable?", "confidence": 0.95},
                                    {"question": "Could research help?", "confidence": 0.2},
                                ]
                            }
                        )
                    }
                }
            ]
        }

        run("extract_open_questions", "--use-ai", "--idea", str(idea.pk))

        entry.refresh_from_db()
        self.assertEqual(entry.open_questions, ["Is the proposed budget acceptable?"])
        post.assert_called_once()
