from django.db import migrations


DEFAULTS = [
    (
        "Owner goals",
        "Represents the project's explicitly documented owner goals and priorities.",
        "Preserve the stated purpose, prefer outcomes that advance it, and keep decisions aligned with recorded human direction.",
        "Do not invent preferences, private facts, budgets, approvals, or goals absent from the idea and its related context.",
    ),
    (
        "Delivery",
        "Protects feasible forward progress and concrete, verifiable next steps.",
        "Prefer the smallest reversible action that produces evidence or completes useful work.",
        "Do not trade away correctness, bypass required checks, or authorize irreversible external action.",
    ),
    (
        "Risk",
        "Examines dependencies, regressions, cost, and conflicts across related ideas.",
        "Keep actions safe, reversible, scoped, and consistent with parent, child, sibling, and dependency constraints.",
        "Abstain when evidence or authority is missing. Never approve destructive, financial, legal, publishing, or external-communication actions autonomously.",
    ),
]


def seed_personas(apps, schema_editor):
    Persona = apps.get_model("ideas", "Persona")
    Idea = apps.get_model("ideas", "Idea")
    IdeaPersona = apps.get_model("ideas", "IdeaPersona")
    personas = []
    for name, description, goals, constraints in DEFAULTS:
        persona, _ = Persona.objects.get_or_create(
            name=name,
            defaults={
                "description": description,
                "goals": goals,
                "constraints": constraints,
                "is_default": True,
                "is_active": True,
            },
        )
        personas.append(persona)
    IdeaPersona.objects.bulk_create(
        [
            IdeaPersona(idea_id=idea_id, persona_id=persona.pk, required=True)
            for idea_id in Idea.objects.values_list("pk", flat=True)
            for persona in personas
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0038_default_personas")]

    operations = [migrations.RunPython(seed_personas, migrations.RunPython.noop)]
