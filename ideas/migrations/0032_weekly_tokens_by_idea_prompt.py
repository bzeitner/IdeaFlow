from django.db import migrations


def upgrade_weekly_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-weekly-summary").first()
    if template is None:
        return
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or '"tokens_by_idea"' in approved.content:
        return
    content = approved.content.replace(
        '"tokens_by_category": {}, "total_tokens"',
        '"tokens_by_category": {}, "tokens_by_idea": {}, "total_tokens"',
    ).replace(
        "and parent idea category; omit unknown token counts rather than estimating.",
        'parent idea category, and idea. Use "Idea #<id> — <title>" as each tokens_by_idea key; omit unknown token counts rather than estimating.',
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
        change_summary="Add per-idea token totals to weekly summary metrics.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0031_execution_model_attribution")]

    operations = [
        migrations.RunPython(upgrade_weekly_prompt, migrations.RunPython.noop),
    ]
