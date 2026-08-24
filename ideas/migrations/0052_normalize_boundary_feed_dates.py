from datetime import datetime, timezone

from django.db import migrations


def normalize_boundary_dates(apps, schema_editor):
    FeedItem = apps.get_model("ideas", "FeedItem")
    FeedItem.objects.filter(
        published_at__lt=datetime(2, 1, 1, tzinfo=timezone.utc)
    ).update(published_at=None)


class Migration(migrations.Migration):
    dependencies = [("ideas", "0051_idea_feed_ingestion_paused")]

    operations = [migrations.RunPython(normalize_boundary_dates, migrations.RunPython.noop)]
