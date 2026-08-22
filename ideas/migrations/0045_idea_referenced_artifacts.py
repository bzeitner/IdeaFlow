from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ideas", "0044_profile_last_seen_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="idea",
            name="referenced_artifacts",
            field=models.ManyToManyField(
                blank=True,
                help_text="Artifacts from other ideas owned by the same person.",
                related_name="referencing_ideas",
                to="ideas.artifact",
            ),
        ),
    ]
