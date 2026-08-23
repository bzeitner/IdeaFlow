from django.db import migrations


def seed(apps, schema_editor):
    Category = apps.get_model("ideas", "Category")
    Category.objects.get_or_create(
        slug="podcast",
        defaults={"name": "Podcast", "color": "#0f7a8c", "order": 5},
    )


def unseed(apps, schema_editor):
    """Only removes the seed if it's still unused, so a reverse can't delete
    a category already in play."""
    Category = apps.get_model("ideas", "Category")
    Category.objects.filter(slug="podcast", ideas__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("ideas", "0049_profile_role_podcast")]

    operations = [migrations.RunPython(seed, unseed)]
