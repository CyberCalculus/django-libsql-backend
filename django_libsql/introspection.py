"""
Introspection for the libSQL/Turso backend.

Delegates to Django's built-in SQLite DatabaseIntrospection via lazy import
to avoid circular import issues at module-load time.
"""


class DatabaseIntrospection:
    """Proxy that lazily imports SQLite's introspection on first use."""

    def __init__(self, connection, *args, **kwargs):
        from django.db.backends.sqlite3.introspection import (
            DatabaseIntrospection as _SQLiteIntrospection,
        )

        self._wrapped = _SQLiteIntrospection(connection, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)
