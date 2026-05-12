"""
Schema editor for the libSQL/Turso backend.

Delegates to Django's built-in SQLite DatabaseSchemaEditor via lazy import
to avoid circular import issues at module-load time.

Turso's HTTP API is stateless — each request is a separate SQLite connection.
PRAGMA settings (like foreign_keys) do not persist across requests. Because of
this, we override __enter__/__exit__ to skip FK constraint toggling, which
would be meaningless across stateless HTTP calls anyway.
"""


class DatabaseSchemaEditor:
    """Proxy that lazily imports SQLite's schema editor on first use."""

    def __init__(self, connection, *args, **kwargs):
        from django.db.backends.sqlite3.schema import (
            DatabaseSchemaEditor as _SQLiteSchemaEditor,
        )

        self._wrapped = _SQLiteSchemaEditor(connection, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __enter__(self):
        # Skip SQLite's FK-constraint-disabled check. Turso HTTP is stateless:
        # each request is an independent connection, so PRAGMA foreign_keys=OFF
        # on one request has no effect on the next. We bypass the base schema
        # editor's __enter__ entirely and go straight to its parent.
        from django.db.backends.base.schema import BaseDatabaseSchemaEditor

        return BaseDatabaseSchemaEditor.__enter__(self._wrapped)

    def __exit__(self, *args):
        # Skip check_constraints() and enable_constraint_checking() — they
        # would run on new HTTP connections, not the connections that executed
        # the schema changes.
        from django.db.backends.base.schema import BaseDatabaseSchemaEditor

        return BaseDatabaseSchemaEditor.__exit__(self._wrapped, *args)
