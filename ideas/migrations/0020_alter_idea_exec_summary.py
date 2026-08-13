from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ideas", "0019_semantic_graph_settings")]

    operations = [
        migrations.AlterField(
            model_name="idea",
            name="exec_summary",
            field=models.TextField(
                blank=True,
                help_text="Human-readable summary of the latest effort's outcome "
                "and recommended next steps (kept current by agents).",
            ),
        ),
    ]
