from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("ideas", "0041_relationship_council_reviews")]

    operations = [
        migrations.CreateModel(
            name="Artifact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("kind", models.CharField(choices=[("report", "Report"), ("list", "List"), ("summary", "Summary")], default="report", max_length=16)),
                ("description", models.TextField(blank=True)),
                ("file", models.FileField(blank=True, upload_to="artifacts/%Y/%m/")),
                ("url", models.URLField(blank=True, help_text="Use for an artifact hosted elsewhere.")),
                ("generated_at", models.DateTimeField(default=django.utils.timezone.now, help_text="When this version of the artifact was generated.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("idea", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="ideas.idea")),
                ("research_entry", models.ForeignKey(blank=True, help_text="The research effort that most recently generated or updated this artifact.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="artifacts", to="ideas.researchentry")),
            ],
            options={"ordering": ["-generated_at", "-updated_at"]},
        ),
        migrations.AddField(
            model_name="idea",
            name="summary_requested_at",
            field=models.DateTimeField(blank=True, help_text="When set, the agent queue will generate or refresh the idea's Summary artifact.", null=True),
        ),
        migrations.AddConstraint(
            model_name="artifact",
            constraint=models.UniqueConstraint(condition=models.Q(("kind", "summary")), fields=("idea",), name="one_summary_artifact_per_idea"),
        ),
    ]
