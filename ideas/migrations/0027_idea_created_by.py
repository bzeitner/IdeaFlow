from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def assign_existing_ideas(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    Idea = apps.get_model("ideas", "Idea")
    owner = User.objects.filter(email__iexact="bzeitner@gmail.com").first()
    if owner is not None:
        Idea.objects.filter(created_by__isnull=True).update(created_by=owner)


class Migration(migrations.Migration):
    dependencies = [
        ("ideas", "0026_update_build_execution_prompt"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="idea",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Owner responsible for this idea.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ideas_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(assign_existing_ideas, migrations.RunPython.noop),
    ]
