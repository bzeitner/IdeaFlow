from django.db import migrations

MODELS = ("CalibrationPlan", "CalibrationAttempt", "CalibrationReview", "CalibrationReport", "CaseEvaluationResult")


def install(apps, schema_editor):
    quote = schema_editor.quote_name
    vendor = schema_editor.connection.vendor
    for name in MODELS:
        table = apps.get_model("evaluations", name)._meta.db_table
        operations = ("UPDATE", "DELETE", "TRUNCATE") if vendor == "postgresql" else ("UPDATE", "DELETE")
        for operation in operations:
            trigger = quote(f"{table}_no_{operation.lower()}")
            if vendor == "sqlite":
                schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {quote(table)} BEGIN SELECT RAISE(ABORT, 'Evaluation audit records are immutable'); END")
            elif vendor == "postgresql":
                level = "STATEMENT" if operation == "TRUNCATE" else "ROW"
                schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {quote(table)} FOR EACH {level} EXECUTE FUNCTION evaluations_reject_mutation()")
            else:
                raise RuntimeError("Evaluation audit guards support PostgreSQL and SQLite only.")


def uninstall(apps, schema_editor):
    quote = schema_editor.quote_name
    vendor = schema_editor.connection.vendor
    for name in MODELS:
        table = apps.get_model("evaluations", name)._meta.db_table
        operations = ("update", "delete", "truncate") if vendor == "postgresql" else ("update", "delete")
        for operation in operations:
            suffix = f" ON {quote(table)}" if vendor == "postgresql" else ""
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {quote(table + '_no_' + operation)}{suffix}")


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0007_calibrationplan_calibrationattempt_calibrationreport_and_more")]
    operations = [migrations.RunPython(install, uninstall)]
