from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("ideas", "0052_normalize_boundary_feed_dates")]

    operations = [
        migrations.AddField(
            model_name="artifact",
            name="presentation_mode",
            field=models.CharField(
                choices=[
                    ("auto", "Choose automatically"),
                    ("report", "Formatted report"),
                    ("table", "Table"),
                    ("structured", "Structured data"),
                    ("raw", "Raw text"),
                    ("embedded", "Embedded document"),
                ],
                blank=True,
                default="auto",
                help_text="Choose Auto unless this artifact needs a specific primary view.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="artifact",
            name="source_format",
            field=models.CharField(
                blank=True,
                help_text="Optional format hint such as markdown, csv, json, html, or plain.",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="default_feed_sort",
            field=models.CharField(
                choices=[
                    ("published_desc", "Newest published"),
                    ("published_asc", "Oldest published"),
                    ("downloaded_desc", "Newest downloaded"),
                    ("feed", "Feed title"),
                    ("idea", "Idea title"),
                    ("category", "Topic"),
                ],
                default="published_desc",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="default_landing_page",
            field=models.CharField(
                choices=[("home", "Public projects"), ("current", "Current"), ("tracking", "Tracking"), ("feeds", "Feeds")],
                default="current",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="default_new_idea_public",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profile",
            name="default_new_idea_status",
            field=models.CharField(
                choices=[("current", "Current"), ("tracking", "Tracking"), ("archived", "Archived")],
                default="current",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="default_owner_scope",
            field=models.CharField(choices=[("all", "All owners"), ("mine", "My ideas")], default="all", max_length=12),
        ),
        migrations.AddField(
            model_name="profile",
            name="default_tracking_sort",
            field=models.CharField(
                choices=[("questions", "Human input needed"), ("family", "Parent and children"), ("rank", "Rank"), ("interest", "Interest"), ("updated", "Last update"), ("oldest", "Needs review")],
                default="questions",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="list_density",
            field=models.CharField(choices=[("comfortable", "Comfortable"), ("compact", "Compact")], default="comfortable", max_length=16),
        ),
        migrations.AddField(
            model_name="profile",
            name="timezone_name",
            field=models.CharField(default="America/Los_Angeles", max_length=64),
        ),
    ]
