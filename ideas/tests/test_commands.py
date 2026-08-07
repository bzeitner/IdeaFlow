import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ideas.models import ResearchEntry, Status

from .helpers import make_idea, make_stage


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

    def test_dump_list_filtered_by_status(self):
        make_idea(title="A", status=Status.CURRENT)
        make_idea(title="B", status=Status.TRACKING)
        data = json.loads(run("dump_idea", "--status", "tracking"))
        self.assertEqual([i["title"] for i in data["ideas"]], ["B"])

    def test_unknown_idea_raises(self):
        with self.assertRaises(CommandError):
            run("dump_idea", "999999")


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
