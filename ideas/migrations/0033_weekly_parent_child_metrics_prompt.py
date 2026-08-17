from django.db import migrations


def upgrade_weekly_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-weekly-summary").first()
    if template is None:
        return
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or '"tasks_by_idea"' in approved.content:
        return
    content = approved.content.replace(
        '"tasks_by_type": {"research": 0, "review": 0, "implementation": 0, "pr_review": 0, "repeat": 0, "other": 0},',
        '"tasks_by_type": {"research": 0, "review": 0, "implementation": 0, "pr_review": 0, "repeat": 0, "other": 0}, "tasks_by_idea": {},',
    ).replace(
        '"tokens_by_idea": {}, "total_tokens": 0',
        '"tokens_by_idea": {}, "total_tasks": 0, "total_tokens": 0',
    ).replace(
        'Use "Idea #<id> — <title>" as each tokens_by_idea key; omit unknown token counts rather than estimating.',
        'Use "Idea #<id> — <title>" in both tasks_by_idea and tokens_by_idea. For every parent with children, include individual rows for the parent and each child plus an "Idea #<parent-id> — <parent-title> + children (total)" row in both groups. Family totals must equal the parent plus its children, while total_tasks and total_tokens remain portfolio totals and must not double-count family-total rows. Omit unknown token counts rather than estimating.',
    )
    if content == approved.content:
        return
    approved.status = "superseded"
    approved.save(update_fields=["status"])
    latest = template.revisions.order_by("-version").values_list("version", flat=True).first() or 0
    PromptRevision.objects.create(
        template=template,
        version=latest + 1,
        content=content,
        status="approved",
        change_summary="Add individual and family-total task and token metrics for child ideas.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0032_weekly_tokens_by_idea_prompt")]

    operations = [migrations.RunPython(upgrade_weekly_prompt, migrations.RunPython.noop)]
