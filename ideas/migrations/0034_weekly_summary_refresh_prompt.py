from django.db import migrations


OLD_QUEUE = """Call $IFCLI weekly-summaries first. Its missing_periods array is the authoritative work queue of completed Sunday-Saturday periods that contain IdeaFlow activity but have no summary. If it is empty, exit successfully."""


def upgrade_weekly_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-weekly-summary").first()
    if template is None:
        return
    variables = list(template.variables or [])
    if "QUEUE_INSTRUCTION" not in variables:
        variables.append("QUEUE_INSTRUCTION")
        template.variables = variables
        template.save(update_fields=["variables"])
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or "$QUEUE_INSTRUCTION" in approved.content:
        return
    content = approved.content.replace(OLD_QUEUE, "$QUEUE_INSTRUCTION")
    content = content.replace(
        "Create IdeaFlow's missing weekly portfolio executive summaries.",
        "Create IdeaFlow's weekly portfolio executive summaries.",
    )
    content = content.replace("each missing period", "each queued period")
    content = content.replace("every missing period", "every queued period")
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
        change_summary="Support explicit regeneration of existing weekly summaries.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0033_weekly_parent_child_metrics_prompt")]

    operations = [migrations.RunPython(upgrade_weekly_prompt, migrations.RunPython.noop)]
