import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def install_guard(apps, schema_editor):
    quote = schema_editor.quote_name
    table = apps.get_model("evaluations", "EvaluatorApprovalSupersession")._meta.db_table
    operations = ("UPDATE", "DELETE", "TRUNCATE") if schema_editor.connection.vendor == "postgresql" else ("UPDATE", "DELETE")
    for operation in operations:
        trigger = quote(f"{table}_no_{operation.lower()}")
        if schema_editor.connection.vendor == "sqlite":
            schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {quote(table)} BEGIN SELECT RAISE(ABORT, 'Evaluation audit records are immutable'); END")
        elif schema_editor.connection.vendor == "postgresql":
            level = "STATEMENT" if operation == "TRUNCATE" else "ROW"
            schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {quote(table)} FOR EACH {level} EXECUTE FUNCTION evaluations_reject_mutation()")
        else:
            raise RuntimeError("Evaluation audit guards support PostgreSQL and SQLite only.")


def uninstall_guard(apps, schema_editor):
    quote = schema_editor.quote_name
    table = apps.get_model("evaluations", "EvaluatorApprovalSupersession")._meta.db_table
    operations = ("update", "delete", "truncate") if schema_editor.connection.vendor == "postgresql" else ("update", "delete")
    for operation in operations:
        suffix = f" ON {quote(table)}" if schema_editor.connection.vendor == "postgresql" else ""
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {quote(table + '_no_' + operation)}{suffix}")


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0008_calibration_audit_guards")]

    operations = [
        migrations.CreateModel(
            name="EvaluatorApprovalSupersession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor_label", models.CharField(max_length=160)),
                ("content_hash", models.CharField(editable=False, max_length=64, validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$", "Expected a SHA-256 hash.")])),
                ("reason", models.TextField()),
                ("approval", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="supersession", to="evaluations.evaluatorapproval")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="evaluations.calibrationplan")),
                ("review", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="evaluations.calibrationreview")),
            ],
            options={"abstract": False},
        ),
        migrations.RunPython(install_guard, uninstall_guard),
    ]
