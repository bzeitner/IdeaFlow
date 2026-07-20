from django.db import migrations

CATEGORIES = [
    ("Project", "project", "#24509b"),
    ("Side Project", "side-project", "#216c40"),
    ("Passive Income", "passive-income", "#8a5a12"),
    ("Research Effort", "research-effort", "#5b3ba0"),
    ("Focus Project", "focus-project", "#99302f"),
]

STAGES = [
    ("Spark", "spark", "#8a5a12"),
    ("Exploring", "exploring", "#5b3ba0"),
    ("Building", "building", "#24509b"),
    ("Launched", "launched", "#216c40"),
    ("Stalled", "stalled", "#99302f"),
]


def seed(apps, schema_editor):
    for model_name, rows in (("Category", CATEGORIES), ("Stage", STAGES)):
        model = apps.get_model("ideas", model_name)
        for order, (name, slug, color) in enumerate(rows):
            model.objects.get_or_create(
                slug=slug, defaults={"name": name, "color": color, "order": order}
            )


def unseed(apps, schema_editor):
    """Only removes seeds still unused, so a reverse can't delete a category in play."""
    for model_name, rows in (("Category", CATEGORIES), ("Stage", STAGES)):
        model = apps.get_model("ideas", model_name)
        model.objects.filter(
            slug__in=[slug for _, slug, _ in rows], ideas__isnull=True
        ).delete()


class Migration(migrations.Migration):
    dependencies = [("ideas", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
