from django.db import migrations

AI_MODELS = [
    ("Claude Opus 4.8", "claude-opus-4-8", "#5b3ba0"),
    ("Claude Sonnet 5", "claude-sonnet-5", "#24509b"),
    ("Claude Haiku 4.5", "claude-haiku-4-5", "#216c40"),
    ("GPT-5", "gpt-5", "#8a5a12"),
    ("Gemini 3", "gemini-3", "#99302f"),
    ("Other", "other", "#44506a"),
]


def seed(apps, schema_editor):
    AIModel = apps.get_model("ideas", "AIModel")
    for order, (name, slug, color) in enumerate(AI_MODELS):
        AIModel.objects.get_or_create(
            slug=slug, defaults={"name": name, "color": color, "order": order}
        )


def unseed(apps, schema_editor):
    """Only removes seeds still unused, so a reverse can't delete a model in play."""
    AIModel = apps.get_model("ideas", "AIModel")
    AIModel.objects.filter(
        slug__in=[slug for _, slug, _ in AI_MODELS], research_entries__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("ideas", "0003_aimodel_alter_category_color_alter_stage_color_and_more")]

    operations = [migrations.RunPython(seed, unseed)]
