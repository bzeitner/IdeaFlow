from django.db import migrations


REPLACEMENTS = [
    (
        """4. Choose the review action from the evidence: request changes only for blocking
   issues; comment for non-blocking findings or questions; approve when no
   material issue remains. Use the matching gh pr review action.""",
        """4. Choose the review action from the evidence: request changes only for blocking
   issues; comment for non-blocking findings or questions; approve when no
   issue remains. Use the matching gh pr review action. A clean review is not
   finished at approval: verify required checks pass, merge the PR using a
   repository-supported merge method, then verify gh pr view <url> --json state
   reports MERGED. If branch protection only requires pending checks,
   enable auto-merge when the repository permits it. Never merge with a failing
   required check, an unresolved finding, or an uncertain merge state.""",
    ),
    (
        "6. Record it — not done until this succeeds:",
        """6. If the PR was merged, run $IFCLI reconcile-pr $ID --url '<PR_URL>'
   --state MERGED to remove its resource and complete the active review action.
   Record the effort after reconciliation. For a merged PR, omit --next-action
   so the existing queued action (if any) remains active. Otherwise set a
   concrete next action for the named finding, failed check, or merge blocker:""",
    ),
    (
        "--next-action '<fix named blockers; address named nits; or merge the PR>'",
        "[--next-action '<fix named finding or resolve named check/merge blocker>']",
    ),
    (
        """   the effort is logged, and its next action matches the verdict. Print one of:
   request-changes, comment-with-nits, or approve.""",
        """   and the effort is logged. When no issue was found, the PR is verified MERGED
   and reconciled in IdeaFlow; otherwise its next action matches the verdict.
   Print one of: request-changes, comment-with-findings, blocked-by-checks, or
   approved-and-merged.""",
    ),
]


def upgrade_critique_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template = PromptTemplate.objects.filter(key="agent-critique").first()
    if template is None:
        return
    approved = template.revisions.filter(status="approved").order_by("-version").first()
    if approved is None or "approved-and-merged" in approved.content:
        return
    content = approved.content
    for old, new in REPLACEMENTS:
        content = content.replace(old, new)
    if content == approved.content:
        return
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
        change_summary="Merge and reconcile pull requests after a clean agent review.",
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0035_weekly_pr_reconciliation_prompt")]

    operations = [
        migrations.RunPython(upgrade_critique_prompt, migrations.RunPython.noop),
    ]
