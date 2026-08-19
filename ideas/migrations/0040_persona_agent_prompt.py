from django.db import migrations


PROMPT = """Review stalled IdeaFlow idea $ID as its configured persona council. Use "$IFCLI" for IdeaFlow. This task may authorize only a reversible next action.

1. Dump the idea and confirm persona_review is enabled, due, and has active required personas. Read graph-context $ID --depth 2 for the parent, children, siblings, dependencies, and dependents. Treat related ideas as decision context, not additional voters.
2. Evaluate each required persona independently from its description, goals, constraints, and the same evidence snapshot. Each must explicitly approve, reject, or abstain. Abstain whenever authority, private context, or evidence is missing.
3. Synthesize one concrete, bounded next action. It must be reversible and must not spend money, publish, delete data, merge or close work, contact external people, change permissions, enter commitments, or claim human approval.
4. Write exactly one JSON object to $REPORT with proposal (summary, action_type chosen from research, analysis, draft, prototype, test, or planning; next_action; reversible=true; and optional question_answers containing research_entry_id, question_index, and answer) and votes (persona_id, decision, rationale). Include one unique vote for every required persona. Consensus requires every required vote to be approve. Include an answer only when it follows directly from documented persona goals; it remains persona-consensus provenance, never a human answer.
5. Submit it with $IFCLI submit-persona-review $ID --review-file $REPORT. The server enforces unanimity and will not act on rejection or abstention. Do not log a separate effort or mutate the idea another way.

$SHARED_STANDARDS"""


def seed_prompt(apps, schema_editor):
    PromptTemplate = apps.get_model("ideas", "PromptTemplate")
    PromptRevision = apps.get_model("ideas", "PromptRevision")
    template, _ = PromptTemplate.objects.get_or_create(
        key="agent-persona",
        defaults={
            "name": "Agent workflow: Persona council",
            "description": "Required-persona review of a stalled idea.",
            "variables": ["ID", "IFCLI", "REPORT", "SHARED_STANDARDS"],
        },
    )
    PromptRevision.objects.get_or_create(
        template=template,
        version=1,
        defaults={
            "content": PROMPT,
            "status": "approved",
            "change_summary": "Add unanimous, reversible persona council reviews.",
        },
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0039_seed_persona_council")]

    operations = [migrations.RunPython(seed_prompt, migrations.RunPython.noop)]
