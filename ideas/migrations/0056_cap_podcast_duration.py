from django.core.validators import MaxValueValidator
from django.db import migrations, models


def cap_existing_durations(apps, schema_editor):
    PodcastShow = apps.get_model("ideas", "PodcastShow")
    PodcastShow.objects.filter(target_episode_duration_seconds__gt=3600).update(
        target_episode_duration_seconds=3600
    )


class Migration(migrations.Migration):
    dependencies = [("ideas", "0055_helpmessage_preserve_deleted_sender")]

    operations = [
        migrations.RunPython(cap_existing_durations, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="podcastshow",
            name="target_episode_duration_seconds",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Maximum 1 hour (3,600 seconds).",
                null=True,
                validators=[MaxValueValidator(3600)],
            ),
        ),
    ]
