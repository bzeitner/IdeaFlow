"""Guard immutable audit content even through bulk ORM or direct SQL writes.

Rollback removes guards only; disabling writers is the production rollback.
Tables and their content are not removed by reversing this migration.
"""
from django.db import migrations

MODELS = ("MetricDefinition", "EvaluatorVersion", "EvaluatorApproval", "EvaluationResult")


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    quote = schema_editor.quote_name
    if vendor == "postgresql":
        schema_editor.execute("""
            CREATE FUNCTION evaluations_reject_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Evaluation audit records are immutable' USING ERRCODE = '23000';
            END; $$
        """)
    elif vendor != "sqlite":
        raise RuntimeError("Evaluation audit guards support PostgreSQL and SQLite only.")
    for name in MODELS:
        table = apps.get_model("evaluations", name)._meta.db_table
        for operation in ("UPDATE", "DELETE"):
            trigger = quote(f"{table}_no_{operation.lower()}")
            if vendor == "sqlite":
                schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {quote(table)} BEGIN SELECT RAISE(ABORT, 'Evaluation audit records are immutable'); END")
            else:
                schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {quote(table)} FOR EACH ROW EXECUTE FUNCTION evaluations_reject_mutation()")
        if vendor == "postgresql":
            schema_editor.execute(f"CREATE TRIGGER {quote(table + '_no_truncate')} BEFORE TRUNCATE ON {quote(table)} FOR EACH STATEMENT EXECUTE FUNCTION evaluations_reject_mutation()")
    table = quote(apps.get_model("evaluations", "EvaluatorDefinition")._meta.db_table)
    if vendor == "sqlite":
        schema_editor.execute(f"CREATE TRIGGER evaluations_identity_no_rename BEFORE UPDATE OF key ON {table} WHEN NEW.key != OLD.key BEGIN SELECT RAISE(ABORT, 'Evaluator identity keys cannot be renamed'); END")
    else:
        schema_editor.execute(f"CREATE TRIGGER evaluations_identity_no_rename BEFORE UPDATE OF key ON {table} FOR EACH ROW WHEN (NEW.key IS DISTINCT FROM OLD.key) EXECUTE FUNCTION evaluations_reject_mutation()")


def uninstall(apps, schema_editor):
    quote = schema_editor.quote_name
    vendor = schema_editor.connection.vendor
    table = quote(apps.get_model("evaluations", "EvaluatorDefinition")._meta.db_table)
    suffix = f" ON {table}" if vendor == "postgresql" else ""
    schema_editor.execute(f"DROP TRIGGER IF EXISTS evaluations_identity_no_rename{suffix}")
    for name in MODELS:
        table = apps.get_model("evaluations", name)._meta.db_table
        operations = ("update", "delete", "truncate") if vendor == "postgresql" else ("update", "delete")
        for operation in operations:
            suffix = f" ON {quote(table)}" if vendor == "postgresql" else ""
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {quote(table + '_no_' + operation)}{suffix}")
    if vendor == "postgresql":
        schema_editor.execute("DROP FUNCTION IF EXISTS evaluations_reject_mutation()")


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0001_initial")]
    operations = [migrations.RunPython(install, uninstall)]
