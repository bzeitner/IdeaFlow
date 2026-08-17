from django.db import migrations, models


def upgrade_agent_prompts(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    for template in PromptTemplate.objects.filter(
        key__in=["agent-research", "agent-review", "agent-execute", "agent-critique"]
    ):
        variables = list(template.variables or [])
        for name in ("PROVIDER", "EXECUTION_MODEL"):
            if name not in variables:
                variables.append(name)
        template.variables = variables
        template.save(update_fields=["variables"])
        approved = template.revisions.filter(status="approved").order_by("-version").first()
        if approved is None or "--execution-model" in approved.content:
            continue
        content = approved.content.replace(
            "--model $MODEL \\",
            "--model $MODEL \\\n       --provider $PROVIDER --execution-model $EXECUTION_MODEL \\",
        ).replace(
            "--model ${MODEL} \\",
            "--model ${MODEL} \\\n       --provider ${PROVIDER} --execution-model ${EXECUTION_MODEL} \\",
        )
        if content == approved.content:
            continue
        approved.status = "superseded"
        approved.save(update_fields=["status"])
        latest = template.revisions.order_by("-version").values_list("version", flat=True).first() or 0
        PromptRevision.objects.create(
            template=template,
            version=latest + 1,
            content=content,
            status="approved",
            change_summary="Record the actual execution provider and model separately from task routing.",
        )

    template = PromptTemplate.objects.filter(key="agent-weekly-summary").first()
    if template is None:
        return
    variables = list(template.variables or [])
    for name in ("PROVIDER", "EXECUTION_MODEL"):
        if name not in variables:
            variables.append(name)
    template.variables = variables
    template.save(update_fields=["variables"])
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or "--provider $PROVIDER" in approved.content:
        return
    content = approved.content.replace(
        "by its task type, model, and parent idea category",
        "by its task type, execution_model when present (falling back to model for legacy entries), and parent idea category",
    ).replace("--model $MODEL --tokens", "--model $EXECUTION_MODEL --provider $PROVIDER --tokens")
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
        change_summary="Attribute weekly summaries and token metrics to the actual executor.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0030_weekly_summary_open_prs_prompt")]

    operations = [
        migrations.AddField(
            model_name="researchentry",
            name="execution_model",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="researchentry",
            name="execution_provider",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="weeklysummary",
            name="execution_provider",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.RunPython(upgrade_agent_prompts, migrations.RunPython.noop),
    ]
