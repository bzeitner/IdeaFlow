from django.db import migrations


OLD_SCHEMA = '{"tasks_by_type": {"research": 0, "review": 0, "implementation": 0, "pr_review": 0, "repeat": 0, "other": 0}, "prs": {"created": 0, "reviewed": 0, "closed": 0}, "tokens_by_task": {}, "tokens_by_model": {}, "tokens_by_category": {}, "total_tokens": 0}'
NEW_SCHEMA = '{"tasks_by_type": {"research": 0, "review": 0, "implementation": 0, "pr_review": 0, "repeat": 0, "other": 0}, "prs": {"created": 0, "reviewed": 0, "closed": 0}, "open_prs": [], "tokens_by_task": {}, "tokens_by_model": {}, "tokens_by_category": {}, "total_tokens": 0}'
ANCHOR = "Token totals come from each entry's tokens_used and must be grouped consistently by its task type, model, and parent idea category; omit unknown token counts rather than estimating."
OPEN_PR_RULE = """
   For every GitHub pull-request URL associated with work in that week, run gh pr view <url> --json state,title,url at summary-generation time. Add an open_prs item only when that command succeeds and reports state OPEN. Each item must contain url, title, idea_id, and a concise description of the change and what remains to review. Never infer open state from IdeaFlow data, and never include a PR when the GitHub lookup fails, is CLOSED, or is MERGED."""


def upgrade_weekly_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-weekly-summary").first()
    if template is None:
        return
    approved = PromptRevision.objects.filter(template=template, status="approved").order_by("-version").first()
    if approved is None or "open_prs" in approved.content:
        return
    content = approved.content.replace(OLD_SCHEMA, NEW_SCHEMA).replace(ANCHOR, ANCHOR + OPEN_PR_RULE)
    if content == approved.content:
        return
    approved.status = "superseded"
    approved.save(update_fields=["status"])
    latest = PromptRevision.objects.filter(template=template).order_by("-version").values_list("version", flat=True).first() or 0
    PromptRevision.objects.create(
        template=template,
        version=latest + 1,
        content=content,
        status="approved",
        change_summary="Verify and list open GitHub pull requests in each weekly summary.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0029_weekly_summary_metrics")]

    operations = [migrations.RunPython(upgrade_weekly_prompt, migrations.RunPython.noop)]
