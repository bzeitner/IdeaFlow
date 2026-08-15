import json

from django.core.management.base import BaseCommand, CommandError

from ideas.graph.semantic import SemanticAPI
from ideas.models import ResearchEntry
from ideas.reporting import extract_open_questions
from ideas.prompts import approved_prompt


MAX_AI_CONTEXT_CHARS = 12000


def extract_with_ai(api, entry):
    prompt = approved_prompt("open-question-single").format(
        idea_title=entry.idea.title,
        topic=entry.topic,
        report=entry.context[:MAX_AI_CONTEXT_CHARS],
    )
    data = api._post(
        "/chat/completions",
        {
            "model": api.classifier_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You conservatively extract unresolved questions requiring human input.",
                },
                {"role": "user", "content": prompt},
            ],
        },
    )
    content = data["choices"][0]["message"]["content"]
    candidates = json.loads(content).get("questions", [])
    questions = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or float(candidate.get("confidence", 0)) < 0.8:
            continue
        question = str(candidate.get("question", "")).strip()
        if question and question not in questions:
            questions.append(question)
    return questions


class Command(BaseCommand):
    help = "Backfill structured open questions from historical research reports."

    def add_arguments(self, parser):
        parser.add_argument("--idea", type=int, help="Only inspect research for one idea ID.")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Also inspect entries that already have structured questions and merge new ones.",
        )
        parser.add_argument("--limit", type=int, default=100, help="Maximum entries to inspect.")
        parser.add_argument("--dry-run", action="store_true", help="Print findings without saving.")
        parser.add_argument(
            "--use-ai",
            action="store_true",
            help="Use the configured semantic classifier when deterministic parsing finds nothing.",
        )

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be at least 1.")
        entries = ResearchEntry.objects.select_related("idea").exclude(context="")
        if options["idea"]:
            entries = entries.filter(idea_id=options["idea"])
            if not entries.exists():
                raise CommandError(f"Idea {options['idea']} has no research reports.")
        if not options["all"]:
            entries = entries.filter(open_questions=[])

        api = None
        if options["use_ai"]:
            try:
                api = SemanticAPI()
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        inspected = found = updated = failed = 0
        for entry in entries.order_by("occurred_at", "pk")[: options["limit"]]:
            inspected += 1
            try:
                questions = extract_open_questions(entry.context)
                method = "markdown"
                if not questions and api is not None:
                    questions = extract_with_ai(api, entry)
                    method = "ai"
                existing = [str(item).strip() for item in entry.open_questions if str(item).strip()]
                merged = existing + [question for question in questions if question not in existing]
                if not questions:
                    self.stdout.write(f"Entry {entry.pk} (idea {entry.idea_id}): no questions found")
                    continue
                found += len(questions)
                self.stdout.write(
                    f"Entry {entry.pk} (idea {entry.idea_id}, {method}): "
                    + " | ".join(questions)
                )
                if merged != existing and not options["dry_run"]:
                    entry.open_questions = merged
                    entry.save(update_fields=["open_questions"])
                    updated += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"Entry {entry.pk} (idea {entry.idea_id}): {exc}")

        mode = "Dry run" if options["dry_run"] else "Complete"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: inspected {inspected}; found {found} question(s); "
                f"updated {updated} entr{'y' if updated == 1 else 'ies'}; failed {failed}."
            )
        )
        if failed:
            raise CommandError(f"Failed to process {failed} research entr{'y' if failed == 1 else 'ies'}.")
