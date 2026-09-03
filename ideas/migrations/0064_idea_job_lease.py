from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ideas", "0063_pause_feed_ingestion_by_default")]

    operations = [
        migrations.AddField(
            model_name="idea",
            name="job_lease_token_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="idea",
            name="job_lease_workflow",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="idea",
            name="job_lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
