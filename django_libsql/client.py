"""
Database client for the libSQL/Turso backend.

Provides a shell entry point for Turso databases.
"""

from django.db.backends.base.client import BaseDatabaseClient


class DatabaseClient(BaseDatabaseClient):
    executable_name = "turso"

    @classmethod
    def settings_to_cmd_args_env(cls, settings_dict, parameters):
        args = [cls.executable_name, "db", "shell", settings_dict["NAME"]]
        if settings_dict.get("AUTH_TOKEN"):
            args.extend(["--token", settings_dict["AUTH_TOKEN"]])
        if parameters:
            args.extend(parameters)
        return args, None
