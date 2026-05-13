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
        from .base import _is_local_name

        if not _is_local_name(self.connection.settings_dict.get("NAME", "")):
            # Remote: skip FK constraint toggling — PRAGMAs don't persist across
            # stateless HTTP requests. Go straight to the base class.
            from django.db.backends.base.schema import BaseDatabaseSchemaEditor

            return BaseDatabaseSchemaEditor.__enter__(self._wrapped)

        # Local: let Django's SQLite schema editor manage FK constraints.
        return self._wrapped.__enter__()

    def __exit__(self, *args):
        from .base import _is_local_name

        if not _is_local_name(self.connection.settings_dict.get("NAME", "")):
            # Remote: skip check_constraints() / enable_constraint_checking().
            from django.db.backends.base.schema import BaseDatabaseSchemaEditor

            return BaseDatabaseSchemaEditor.__exit__(self._wrapped, *args)

        # Local: let Django's SQLite schema editor run its exit logic.
        return self._wrapped.__exit__(*args)
