"""
Django database backend for libSQL/Turso.

Provides a Django database backend that communicates with remote libSQL/SQLite
databases via Turso's HTTP REST API (Hrana protocol over HTTP) or WebSocket
(Hrana protocol), or uses local SQLite files.

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

The ``NAME`` can be a full URL (``https://db-name.turso.io``), a bare
hostname (``db-name.turso.io``), a ``libsql://`` WebSocket URL, or a local
file path.
"""

from __future__ import annotations

__version__ = "0.1.2"

from .base import DatabaseWrapper

__all__ = ["DatabaseWrapper", "__version__"]
