"""
Schema editor for the libSQL/Turso backend.

Delegates to Django's built-in SQLite DatabaseSchemaEditor via lazy import
to avoid circular import issues at module-load time.

Turso's HTTP API is stateless -- each request is a separate SQLite connection.
PRAGMA settings (like foreign_keys) do not persist across requests. Because of
this, we override __enter__/__exit__ to skip FK constraint toggling, which
would be meaningless across stateless HTTP calls anyway.
"""

from __future__ import annotations


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
        from .base import _get_connection_mode

        mode = _get_connection_mode(self.connection.settings_dict.get("NAME", ""))
        if mode == "http":
            # Stateless HTTP: skip FK constraint toggling — PRAGMAs don't
            # persist across requests. Go straight to the base class.
            from django.db.backends.base.schema import BaseDatabaseSchemaEditor

            BaseDatabaseSchemaEditor.__enter__(self._wrapped)
            return self  # return proxy, not the wrapped object

        # Local or Hrana: FK constraints persist on the connection, so
        # Django's SQLite schema editor can manage them correctly.
        return self._wrapped.__enter__()

    def __exit__(self, *args):
        from .base import _get_connection_mode

        mode = _get_connection_mode(self.connection.settings_dict.get("NAME", ""))
        if mode == "http":
            # Stateless HTTP: skip check_constraints() / enable_constraint_checking().
            from django.db.backends.base.schema import BaseDatabaseSchemaEditor

            return BaseDatabaseSchemaEditor.__exit__(self._wrapped, *args)

        # Local or Hrana: run full exit logic with constraint checking.
        return self._wrapped.__exit__(*args)
