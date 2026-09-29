import django.core.validators
from django.db import migrations, models


def seed_goals(apps, schema_editor):
    """Fill blank goals only, so re-runs never overwrite admin edits."""
    from ideas.category_goals import DEFAULT_GOALS

    Category = apps.get_model("ideas", "Category")
    for slug, (name, text) in DEFAULT_GOALS.items():
        category = (
            Category.objects.filter(slug=slug).first()
            or Category.objects.filter(name__iexact=name).first()
        )
        if category is not None and not category.goal_text:
            category.goal_text = text
            category.save(update_fields=["goal_text"])


class Migration(migrations.Migration):
    dependencies = [("ideas", "0066_semanticgraphsettings_relationship_council_limit")]

    operations = [
        migrations.AddField(
            model_name="category",
            name="goal_text",
            field=models.TextField(
                blank=True,
                help_text="Research goal shown when adding an idea in this category and passed to research agents. Managed on the Category goals admin page.",
                max_length=2000,
                validators=[django.core.validators.MaxLengthValidator(2000)],
            ),
        ),
        migrations.RunPython(seed_goals, migrations.RunPython.noop),
    ]
