import hashlib
import json

from django.db import migrations


WORKFLOWS = (
    ("research", "Research", ["agent-research", "shared-standards"]),
    ("review", "Review", ["agent-review", "shared-standards"]),
    ("execute", "Execute", ["agent-execute", "shared-standards"]),
    ("critique", "Critique", ["agent-critique", "shared-standards"]),
    ("summary", "Summary", ["agent-summary", "shared-standards"]),
    ("repeat", "Repeat discovery", ["agent-repeat", "shared-standards"]),
    ("reflection", "Portfolio reflection", ["agent-portfolio-reflection", "shared-standards"]),
    ("feed_score", "Feed scoring", ["agent-feed-scoring", "shared-standards"]),
    ("weekly_summary", "Weekly summary", ["agent-weekly-summary", "shared-standards"]),
    ("relationship_classification", "Relationship classification", ["semantic-relationship-classification"]),
    ("relationship_council", "Relationship council", ["relationship-council-review", "shared-standards"]),
    ("open_question_extraction", "Open-question extraction", ["open-question-single", "open-question-batch"]),
    ("persona_council", "Persona council", ["agent-persona", "shared-standards"]),
    ("podcast_script", "Podcast script", ["agent-repeat", "shared-standards"]),
)

MODEL_CONFIGURATIONS = (
    ("claude", "claude-haiku-4-5", "light"),
    ("claude", "claude-sonnet-5", "standard"),
    ("claude", "claude-opus-4-8", "heavy"),
    ("codex", "codex-default", "standard"),
    ("openai-compatible", "text-embedding-3-small", "embedding"),
    ("openai-compatible", "gpt-4.1-mini", "light"),
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def seed(apps, schema_editor):
    WorkflowDefinition = apps.get_model("executions", "WorkflowDefinition")
    WorkflowVersion = apps.get_model("executions", "WorkflowVersion")
    ModelConfiguration = apps.get_model("executions", "ModelConfiguration")
    for key, name, prompt_keys in WORKFLOWS:
        workflow, _ = WorkflowDefinition.objects.get_or_create(
            key=key, defaults={"name": name, "description": "Imported Phase 1 production workflow."}
        )
        configuration = {
            "baseline": "phase1-existing-behavior",
            "prompt_keys": prompt_keys,
            "instrumented": False,
        }
        WorkflowVersion.objects.get_or_create(
            workflow=workflow,
            version=1,
            defaults={
                "status": "approved",
                "configuration": configuration,
                "prompt_revision_manifest": [
                    {"key": prompt_key, "revision": "resolve-at-run-start"}
                    for prompt_key in prompt_keys
                ],
                "content_hash": digest(configuration),
            },
        )
    for provider, model_identifier, capability in MODEL_CONFIGURATIONS:
        configuration = {
            "provider": provider,
            "model_identifier": model_identifier,
            "capability": capability,
            "settings": {},
        }
        ModelConfiguration.objects.get_or_create(
            content_hash=digest(configuration),
            defaults={
                "provider": provider,
                "model_identifier": model_identifier,
                "capability": capability,
                "settings": {},
            },
        )


def unseed(apps, schema_editor):
    # Audit configuration may already be referenced by traces. Phase 1 is
    # additive, so rollback removes schema rather than selectively deleting
    # potentially referenced seed rows.
    pass


class Migration(migrations.Migration):
    dependencies = [("executions", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
