"""
Django database backend for libSQL/Turso.

Provides a Django database backend that communicates with remote libSQL/SQLite
databases via Turso's HTTP REST API (Hrana protocol over HTTP).

Usage in Django settings.py::

    DATABASES = {
        "default": {
            "ENGINE": "django_libsql",
            "NAME": "https://your-database.turso.io",
            "AUTH_TOKEN": "your-jwt-auth-token",
            "OPTIONS": {
                "timeout": 30,
            },
        }
    }

The ``NAME`` can be a full URL (``https://db-name.turso.io``) or a bare
hostname (``db-name.turso.io``) — ``https://`` is prepended automatically.
"""

__version__ = "0.1.1"

from .base import DatabaseWrapper

__all__ = ["DatabaseWrapper", "__version__"]
