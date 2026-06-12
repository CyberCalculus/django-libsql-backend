"""
Database client for the libSQL/Turso backend.

Provides a shell entry point for both local SQLite and remote Turso databases.
"""

from __future__ import annotations

from django.db.backends.base.client import BaseDatabaseClient


class DatabaseClient(BaseDatabaseClient):
    executable_name = "turso"

    @classmethod
    def settings_to_cmd_args_env(cls, settings_dict, parameters):
        from .base import _is_local_name

        name = settings_dict.get("NAME", "")

        if _is_local_name(name):
            # Local mode: use sqlite3 CLI directly.
            args = ["sqlite3", name]
            if parameters:
                args.extend(parameters)
            return args, None

        # Remote mode: use turso CLI.
        args = [cls.executable_name, "db", "shell", name]
        if settings_dict.get("AUTH_TOKEN"):
            args.extend(["--token", settings_dict["AUTH_TOKEN"]])
        if parameters:
            args.extend(parameters)
        return args, None
