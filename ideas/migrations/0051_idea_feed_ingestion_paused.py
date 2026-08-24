from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ideas", "0050_podcast_category")]

    operations = [
        migrations.AddField(
            model_name="idea",
            name="feed_ingestion_paused",
            field=models.BooleanField(
                default=False,
                help_text="Pause future feed refreshes for this idea without hiding existing items.",
            ),
        ),
    ]
