"""Freeze dataset metadata; allow content deletion only after an audit tombstone."""
from django.db import migrations

MODELS = ('EvaluationDataset', 'DatasetCase', 'DatasetSnapshot', 'DatasetCaseTombstone', 'HumanCalibrationLabel')
CONTENT = 'evaluations_datasetcasecontent'
TOMBSTONE = 'evaluations_datasetcasetombstone'


def install(apps, schema_editor):
    q = schema_editor.quote_name
    vendor = schema_editor.connection.vendor
    for name in MODELS:
        table = apps.get_model('evaluations', name)._meta.db_table
        for operation in (('UPDATE', 'DELETE', 'TRUNCATE') if vendor == 'postgresql' else ('UPDATE', 'DELETE')):
            trigger = q(f'{table}_no_{operation.lower()}')
            if vendor == 'sqlite':
                schema_editor.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {q(table)} BEGIN SELECT RAISE(ABORT, 'Dataset audit records are immutable'); END")
            elif vendor == 'postgresql':
                level = 'STATEMENT' if operation == 'TRUNCATE' else 'ROW'
                schema_editor.execute(f'CREATE TRIGGER {trigger} BEFORE {operation} ON {q(table)} FOR EACH {level} EXECUTE FUNCTION evaluations_reject_mutation()')
            else:
                raise RuntimeError('Dataset guards support PostgreSQL and SQLite only.')
    if vendor == 'sqlite':
        schema_editor.execute(f"CREATE TRIGGER dataset_content_no_update BEFORE UPDATE ON {CONTENT} BEGIN SELECT RAISE(ABORT, 'Dataset content is immutable'); END")
        schema_editor.execute(f"CREATE TRIGGER dataset_content_delete_guard BEFORE DELETE ON {CONTENT} WHEN NOT EXISTS (SELECT 1 FROM {TOMBSTONE} WHERE case_id=OLD.case_id) BEGIN SELECT RAISE(ABORT, 'Dataset deletion needs a tombstone'); END")
        schema_editor.execute(f"CREATE TRIGGER dataset_content_insert_guard BEFORE INSERT ON {CONTENT} WHEN EXISTS (SELECT 1 FROM {TOMBSTONE} WHERE case_id=NEW.case_id) BEGIN SELECT RAISE(ABORT, 'Deleted dataset content cannot be restored'); END")
    else:
        schema_editor.execute(f'''CREATE FUNCTION evaluations_guard_dataset_content() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF NOT EXISTS (SELECT 1 FROM {TOMBSTONE} WHERE case_id=OLD.case_id) THEN
                        RAISE EXCEPTION 'Dataset deletion needs a tombstone' USING ERRCODE = '23000';
                    END IF;
                    RETURN OLD;
                END IF;
                IF EXISTS (SELECT 1 FROM {TOMBSTONE} WHERE case_id=NEW.case_id) THEN
                    RAISE EXCEPTION 'Deleted dataset content cannot be restored' USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END; $$''')
        schema_editor.execute(f'CREATE TRIGGER dataset_content_no_update BEFORE UPDATE ON {CONTENT} FOR EACH ROW EXECUTE FUNCTION evaluations_reject_mutation()')
        schema_editor.execute(f'CREATE TRIGGER dataset_content_no_truncate BEFORE TRUNCATE ON {CONTENT} FOR EACH STATEMENT EXECUTE FUNCTION evaluations_reject_mutation()')
        for op in ('INSERT', 'DELETE'):
            schema_editor.execute(f'CREATE TRIGGER dataset_content_{op.lower()}_guard BEFORE {op} ON {CONTENT} FOR EACH ROW EXECUTE FUNCTION evaluations_guard_dataset_content()')


def uninstall(apps, schema_editor):
    q = schema_editor.quote_name
    vendor = schema_editor.connection.vendor
    for name in MODELS:
        table = apps.get_model('evaluations', name)._meta.db_table
        for operation in (('update', 'delete', 'truncate') if vendor == 'postgresql' else ('update', 'delete')):
            suffix = f' ON {q(table)}' if vendor == 'postgresql' else ''
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {q(table + "_no_" + operation)}{suffix}')
    for name in ('dataset_content_no_update', 'dataset_content_no_truncate', 'dataset_content_delete_guard', 'dataset_content_insert_guard'):
        suffix = f' ON {CONTENT}' if vendor == 'postgresql' else ''
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}{suffix}')
    if vendor == 'postgresql':
        schema_editor.execute('DROP FUNCTION IF EXISTS evaluations_guard_dataset_content()')


class Migration(migrations.Migration):
    dependencies = [('evaluations', '0005_datasetcase_datasetcasecontent_datasetcasetombstone_and_more')]
    operations = [migrations.RunPython(install, uninstall)]
