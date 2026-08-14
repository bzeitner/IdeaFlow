from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("ideas", "0022_idea_next_actions")]

    operations = [
        migrations.AddField(model_name="idea", name="repeat_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="idea", name="repeat_paused", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="idea", name="repeat_goal", field=models.TextField(blank=True, help_text="Measurable goal for each repeat run, such as finding local job leads.")),
        migrations.AddField(model_name="idea", name="repeat_target_count", field=models.PositiveSmallIntegerField(default=5)),
        migrations.AddField(model_name="idea", name="repeat_interval_days", field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name="idea", name="last_repeat_run_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.CreateModel(
            name="RepeatResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=300)),
                ("url", models.URLField(blank=True, max_length=1000)),
                ("details", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("new", "New"), ("interested", "Interested"), ("actioned", "Applied / Actioned"), ("dismissed", "Dismissed")], default="new", max_length=16)),
                ("found_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("idea", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="repeat_results", to="ideas.idea")),
            ],
            options={"ordering": ["-found_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="repeatresult",
            constraint=models.UniqueConstraint(condition=~models.Q(url=""), fields=("idea", "url"), name="unique_repeat_result_url_per_idea"),
        ),
    ]
