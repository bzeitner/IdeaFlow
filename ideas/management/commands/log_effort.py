"""Record a work-effort report (a ResearchEntry) against an idea.

    manage.py log_effort 12 \
        --topic "Prototype the CSV importer" \
        --model claude-opus-4-8 \
        --context-file report.md \
        --effort 4 --quality 5 --tokens 180000 \
        --repo-url https://github.com/bzeitner/csv-importer --repo-label "Repo" \
        --stage prototyping --status tracking

--context-file wins over --context, so an agent can hand off a long write-up
without shell-quoting it.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ideas.models import Idea, Status
from ideas.reporting import record_effort
from ideas.serialize import idea_to_dict, research_entry_to_dict


class Command(BaseCommand):
    help = "Record a ResearchEntry (work-effort report) against an idea."

    def add_arguments(self, parser):
        parser.add_argument("pk", type=int, help="Idea id to report against.")
        parser.add_argument("--topic", required=True, help="Short title for the entry.")
        parser.add_argument(
            "--model", default="other", help="AI model slug or name (default: other)."
        )
        parser.add_argument("--context", default="", help="The write-up / report body.")
        parser.add_argument(
            "--context-file", help="Read the report body from this file (overrides --context)."
        )
        parser.add_argument("--focus", default="", help="Optional focus/angle.")
        parser.add_argument("--effort", type=int, default=3, help="1-5.")
        parser.add_argument("--quality", type=int, default=3, help="1-5.")
        parser.add_argument(
            "--tokens", type=int, dest="tokens_used", help="Approx tokens used."
        )
        parser.add_argument(
            "--repo-url", dest="resource_url", help="Result link to attach as a Resource."
        )
        parser.add_argument(
            "--repo-label", dest="resource_label", default="", help="Label for that link."
        )
        parser.add_argument("--stage", help="Move the idea to this stage (slug or name).")
        parser.add_argument(
            "--status",
            choices=[s.value for s in Status],
            help="Move the idea to this tab.",
        )
        parser.add_argument(
            "--next-action", dest="next_action", help="Set the idea's next action."
        )
        parser.add_argument(
            "--queue-next-action",
            dest="queued_next_actions",
            action="append",
            default=[],
            help="Queue another action after the active one; repeatable.",
        )
        parser.add_argument(
            "--exec-summary",
            dest="exec_summary",
            help="Set the human-readable latest effort summary and recommendations.",
        )
        parser.add_argument(
            "--open-question",
            dest="open_questions",
            action="append",
            default=[],
            help="Question requiring human input; repeatable.",
        )

    def handle(self, *args, **options):
        try:
            idea = Idea.objects.get(pk=options["pk"])
        except Idea.DoesNotExist:
            raise CommandError(f"No idea with id {options['pk']}.")

        context = options["context"]
        if options["context_file"]:
            try:
                with open(options["context_file"], encoding="utf-8") as fh:
                    context = fh.read()
            except OSError as exc:
                raise CommandError(f"Could not read --context-file: {exc}")

        try:
            entry, resource = record_effort(
                idea,
                topic=options["topic"],
                model=options["model"],
                context=context,
                focus=options["focus"],
                effort=options["effort"],
                quality=options["quality"],
                tokens_used=options["tokens_used"],
                resource_url=options["resource_url"],
                resource_label=options["resource_label"],
                stage=options["stage"],
                status=options["status"],
                next_action=options["next_action"],
                queued_next_actions=options["queued_next_actions"],
                exec_summary=options["exec_summary"],
                open_questions=options["open_questions"],
            )
        except (ValueError, LookupError) as exc:
            raise CommandError(str(exc))

        idea.refresh_from_db()
        self.stdout.write(
            json.dumps(
                {
                    "research_entry": research_entry_to_dict(entry),
                    "resource_added": bool(resource),
                    "idea": idea_to_dict(idea, detail=False),
                },
                indent=2,
                default=str,
            )
        )
        self.stderr.write(
            self.style.SUCCESS(f"Logged effort '{entry.topic}' on idea {idea.pk}.")
        )
