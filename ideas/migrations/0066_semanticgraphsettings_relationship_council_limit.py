from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ideas", "0065_podcastdownload")]

    operations = [
        migrations.AddField(
            model_name="semanticgraphsettings",
            name="relationship_council_daily_check_limit",
            field=models.PositiveSmallIntegerField(
                default=50,
                help_text=(
                    "Maximum relationship suggestions the council may begin per local "
                    "calendar day. Set to 0 to pause council checks."
                ),
            ),
        ),
    ]
