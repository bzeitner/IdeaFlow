from django.db import migrations


WORKFLOWS = (
    "persona_council",
    "relationship_council",
    "weekly_summary",
    "execute",
    "critique",
    "podcast_script",
)


def seed(apps, schema_editor):
    WorkflowCutover = apps.get_model("executions", "WorkflowCutover")
    for key in WORKFLOWS:
        WorkflowCutover.objects.get_or_create(
            workflow_key=key,
            defaults={"mode": "legacy", "reason": "Phase 4 deployed; legacy path remains authoritative."},
        )


class Migration(migrations.Migration):
    dependencies = [("executions", "0004_workflowcutover_artifactversion_deterministicjob_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
