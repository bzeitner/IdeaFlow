from django.db import migrations, models


def seed_next_action_queues(apps, schema_editor):
    Idea = apps.get_model("ideas", "Idea")
    for idea in Idea.objects.exclude(next_action="").iterator():
        idea.next_actions = [idea.next_action]
        idea.save(update_fields=["next_actions"])


class Migration(migrations.Migration):
    dependencies = [("ideas", "0021_feed_backfill_cutoff")]

    operations = [
        migrations.AlterField(
            model_name="idea",
            name="next_action",
            field=models.TextField(
                blank=True,
                help_text="The active (first) item in the queued next actions.",
            ),
        ),
        migrations.AddField(
            model_name="idea",
            name="next_actions",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Ordered queue of next actions; the first item is active.",
            ),
        ),
        migrations.RunPython(seed_next_action_queues, migrations.RunPython.noop),
    ]
