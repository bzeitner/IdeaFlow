import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0009_calibration_plan_and_approval_supersession")]

    operations = [
        migrations.AddField(
            model_name="calibrationreview",
            name="review_mode",
            field=models.CharField(
                choices=[
                    ("independent_blinded_v1", "Independent blinded"),
                    ("model_assisted_error_audit_v1", "Model-assisted error audit"),
                ],
                default="independent_blinded_v1",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="calibrationreview",
            name="assisted_result",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assisted_reviews",
                to="evaluations.caseevaluationresult",
            ),
        ),
        migrations.AddField(
            model_name="calibrationreview",
            name="difference_manifest",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
