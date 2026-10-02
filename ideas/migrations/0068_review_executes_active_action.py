from django.db import migrations


ACTIVE_ACTION_GUIDANCE = """1a. Treat a non-empty active next_action as work to execute, not merely a topic
   to review. Attempt it to completion first whenever it is achievable with the
   tools and mutations this workflow permits. Perform the research, analysis,
   drafting, or artifact creation/update it requires even when no new articles
   arrived. Do not merely restate, recommend, or requeue the same action. If the
   action names an existing artifact, update that exact artifact with
   --artifact-id and verify the upload succeeded. For an artifact action, do the
   verified upload before log-effort; this ordering overrides the general
   artifact guidance below so the action is not marked complete before its
   deliverable exists. When the active action is complete, pass --next-action ''
   to log-effort so the queue advances, unless the evidence justifies a distinct
   follow-up action. If a true blocker or missing authority prevents completion,
   preserve the active action, document the blocker, and use --open-question
   only when human input is required. Never claim completion from a plan or
   recommendation alone.
"""


def upgrade_review_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-review").first()
    if template is None:
        return
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or "active next_action as work to execute" in approved.content:
        return
    marker = "2. Synthesize the existing research_entries"
    if marker not in approved.content:
        raise RuntimeError(
            "Cannot upgrade agent-review prompt: expected synthesis marker is missing."
        )
    content = approved.content.replace(marker, f"{ACTIVE_ACTION_GUIDANCE}{marker}", 1)
    approved.status = "superseded"
    approved.save(update_fields=["status"])
    latest = (
        template.revisions.order_by("-version")
        .values_list("version", flat=True)
        .first()
        or 0
    )
    PromptRevision.objects.create(
        template=template,
        version=latest + 1,
        content=content,
        status="approved",
        change_summary=(
            "Execute concrete active actions during review and verify artifact "
            "updates before advancing the queue."
        ),
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0067_category_goal_text")]

    operations = [
        migrations.RunPython(upgrade_review_prompt, migrations.RunPython.noop),
    ]
