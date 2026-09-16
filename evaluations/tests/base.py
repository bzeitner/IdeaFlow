"""Test-only reset for append-only evaluation tables.

Production guards have no bypass setting. Transaction tests explicitly opt in
to this base class so their fixture flush can temporarily remove schema guards
inside the same transaction as the reset. All guards are restored on commit;
an exception rolls back the guard removal along with the flush.
"""
from contextlib import ExitStack, contextmanager
from importlib import import_module

from django.apps import apps
from django.db import connections, transaction
from django.test import TransactionTestCase
from django.test.utils import _TestState


def _guards_installed(connection):
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE t.tgname = 'evaluations_identity_no_rename' "
                "AND n.nspname = current_schema())"
            )
        elif connection.vendor == "sqlite":
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM sqlite_master "
                "WHERE type = 'trigger' AND name = 'evaluations_identity_no_rename')"
            )
        else:
            return False
        return bool(cursor.fetchone()[0])


@contextmanager
def audit_fixture_reset(alias, expected_name):
    connection = connections[alias]
    if not hasattr(_TestState, "saved_data") or connection.settings_dict["NAME"] != expected_name:
        raise RuntimeError("Audit fixture reset is restricted to the active test database.")
    if not _guards_installed(connection):
        yield
        return
    guards = import_module("evaluations.migrations.0002_immutable_audit_guards")
    with transaction.atomic(using=alias):
        # Execute only trigger DDL: do not enter SQLite's schema-editor context,
        # which attempts to toggle foreign-key checks inside this transaction.
        editor = connection.schema_editor()
        guards.uninstall(apps, editor)
        yield
        guards.install(apps, editor)


class AuditTransactionTestCase(TransactionTestCase):
    @classmethod
    def _pre_setup(cls):
        super()._pre_setup()
        cls._audit_test_names = {
            alias: connections[alias].settings_dict["NAME"]
            for alias in cls._databases_names(include_mirrors=False)
        }

    def _fixture_teardown(self):
        with ExitStack() as stack:
            for alias, name in self._audit_test_names.items():
                stack.enter_context(audit_fixture_reset(alias, name))
            super()._fixture_teardown()
