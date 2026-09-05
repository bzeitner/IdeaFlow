import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ideas", "0064_idea_job_lease")]

    operations = [
        migrations.CreateModel(
            name="PodcastDownload",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("listener_hash", models.CharField(max_length=64)),
                ("download_day", models.DateField()),
                (
                    "requested_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="downloads",
                        to="ideas.episode",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["download_day"],
                        name="ideas_podca_downloa_e5fbbe_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("episode", "listener_hash", "download_day"),
                        name="unique_daily_podcast_download",
                    )
                ],
            },
        ),
    ]
