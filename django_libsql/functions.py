"""
SQL function registration for local sqlite3 connections.

Delegates to Django's built-in SQLite _functions module, which registers
30+ custom SQL functions (date/time extraction, truncation, hashing, math,
aggregates, regex, etc.) on a raw sqlite3.Connection.

For remote (Turso HTTP) connections, these functions are unavailable.
SQL queries that rely on them are rewritten to use native SQLite expressions
in RemoteDatabaseOperations (see operations.py).
"""

from __future__ import annotations

try:
    from django.db.backends.sqlite3._functions import register as register_functions
except ImportError:
    raise ImportError(
        "django_libsql requires Django's sqlite3._functions module. "
        "This module may have moved or been removed in your Django version. "
        "Please upgrade Django or report this issue."
    ) from None

__all__ = ["register_functions"]
