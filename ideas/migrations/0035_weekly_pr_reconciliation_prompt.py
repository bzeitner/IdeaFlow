from django.db import migrations


RECONCILIATION = """2a. Reconcile stale PR state for every GitHub pull-request URL in each dumped idea's resources or active next action. Run gh pr view <url> --json state. Only when that lookup succeeds and state is CLOSED or MERGED, run:
     $IFCLI reconcile-pr <idea-id> --url <url> --state <CLOSED-or-MERGED>
   This removes the stale PR resource and, only when the active next action contains that exact URL, completes it so the next queued action can become active. Never reconcile OPEN PRs or a URL whose lookup fails, and do not infer state from IdeaFlow text.
"""


def upgrade_weekly_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-weekly-summary").first()
    if template is None:
        return
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or "reconcile-pr <idea-id>" in approved.content:
        return
    content = approved.content.replace(
        "do not access a local database or mutate any idea.",
        "do not access a local database. Do not mutate ideas except through the verified PR reconciliation in step 2a.",
    ).replace("3. For each queued period", RECONCILIATION + "3. For each queued period")
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
        change_summary="Reconcile verified closed or merged PRs before weekly reporting.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0034_weekly_summary_refresh_prompt")]

    operations = [migrations.RunPython(upgrade_weekly_prompt, migrations.RunPython.noop)]
