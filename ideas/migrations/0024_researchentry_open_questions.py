from django.db import migrations, models
import re


def backfill_open_questions(apps, schema_editor):
    ResearchEntry = apps.get_model("ideas", "ResearchEntry")
    for entry in ResearchEntry.objects.exclude(context="").iterator():
        questions = []
        in_section = False
        for line in entry.context.splitlines():
            if re.match(r"^\s{0,3}#{1,6}\s+open questions?\s*:?\s*$", line, re.I):
                in_section = True
                continue
            if in_section and re.match(r"^\s{0,3}#{1,6}\s+", line):
                break
            if not in_section:
                continue
            match = re.match(r"^\s*(?:[-*+] |\d+[.)] )(.*\S)\s*$", line)
            if match:
                question = match.group(1).strip()
                if question.lower().rstrip(".") not in {"none", "n/a"}:
                    questions.append(question)
        if questions:
            entry.open_questions = questions
            entry.save(update_fields=["open_questions"])


class Migration(migrations.Migration):
    dependencies = [("ideas", "0023_repeatable_tasks")]

    operations = [
        migrations.AddField(
            model_name="researchentry",
            name="open_questions",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Specific questions the next agent run needs a human to answer.",
            ),
        ),
        migrations.AddField(
            model_name="researchentry",
            name="question_answers",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Human answers keyed by the open question's zero-based index.",
            ),
        ),
        migrations.RunPython(backfill_open_questions, migrations.RunPython.noop),
    ]
