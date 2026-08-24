from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ideas", "0053_ux_preferences_and_artifact_presentation"),
    ]

    operations = [
        migrations.CreateModel(
            name="HelpMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField(max_length=5000)),
                ("admin_response", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sender", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="help_messages_sent", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(help_text="The user whose help conversation this message belongs to.", on_delete=django.db.models.deletion.CASCADE, related_name="help_messages", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddIndex(
            model_name="helpmessage",
            index=models.Index(fields=["user", "created_at"], name="ideas_helpm_user_id_5fbc95_idx"),
        ),
    ]
