from django.db import migrations


OLD = "   arrived. Do not merely"
NEW = """   arrived, including primary-source research the action asks for even when it
   would not change the disposition; the fresh-research limit in step 2 applies
   only to open-ended research beyond the active action. Do not merely"""
DONE = "primary-source research the action asks for"


def upgrade_review_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-review").first()
    if template is None:
        return
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    # Databases that applied the earlier 0068 hold the guidance without the
    # research-scope exemption; fresh databases already include it.
    if approved is None or DONE in approved.content or OLD not in approved.content:
        return
    content = approved.content.replace(OLD, NEW, 1)
    approved.status = "superseded"
    approved.save(update_fields=["status"])
    latest = template.revisions.order_by("-version").values_list("version", flat=True).first() or 0
    PromptRevision.objects.create(
        template=template,
        version=latest + 1,
        content=content,
        status="approved",
        change_summary="Exempt active-action research from the fresh-research limit.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0068_review_executes_active_action")]

    operations = [
        migrations.RunPython(upgrade_review_prompt, migrations.RunPython.noop),
    ]
