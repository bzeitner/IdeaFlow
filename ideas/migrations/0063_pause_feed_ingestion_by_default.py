from django.db import migrations, models


def pause_existing_ideas(apps, schema_editor):
    Idea = apps.get_model("ideas", "Idea")
    Idea.objects.filter(feed_ingestion_paused=False).update(feed_ingestion_paused=True)


class Migration(migrations.Migration):
    dependencies = [("ideas", "0062_episoderun_deterministic_job_and_more")]

    operations = [
        migrations.AlterField(
            model_name="idea",
            name="feed_ingestion_paused",
            field=models.BooleanField(
                default=True,
                help_text="Pause future feed refreshes for this idea without hiding existing items.",
            ),
        ),
        migrations.RunPython(pause_existing_ideas, migrations.RunPython.noop),
    ]
