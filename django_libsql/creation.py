"""
Database creation for the libSQL/Turso backend.

Local mode:
    Creates a separate test database file so tests never touch the
    production database. Supports cloning for parallel test execution.

Remote (Turso HTTP) mode:
    Reuses the production database name since Turso databases are
    provisioned externally. Tables are dropped between test runs.
    TEST["NAME"] can override to point at a dedicated test database.
"""

import os

from django.db.backends.base.creation import BaseDatabaseCreation


class DatabaseCreation(BaseDatabaseCreation):
    @staticmethod
    def is_in_memory_db(database_name):
        """Return True if *database_name* describes an in-memory SQLite DB."""
        return database_name == ":memory:" or "mode=memory" in database_name

    def _get_test_db_name(self):
        """Return the test database name.

        Local mode: returns a ``test_``-prefixed file path derived from the
        production NAME, or uses TEST["NAME"] if configured.

        Remote mode: returns the production NAME (Turso databases cannot be
        created on the fly), or TEST["NAME"] if configured.
        """
        from .base import _is_local_name

        test_name = self.connection.settings_dict.get("TEST", {}).get("NAME")
        if test_name:
            return test_name

        name = self.connection.settings_dict["NAME"]

        if _is_local_name(name):
            if self.is_in_memory_db(name):
                return (
                    "file:memorydb_%s?mode=memory&cache=shared"
                    % self.connection.alias
                )
            root, ext = os.path.splitext(name)
            return f"{root}_test{ext or '.sqlite3'}"

        # Remote: cannot create a new Turso database; reuse production name.
        return name

    def create_test_db(self, verbosity=1, autoclobber=False, keepdb=False):
        test_db_name = self._get_test_db_name()

        if not keepdb:
            self._destroy_test_db(test_db_name, verbosity=verbosity)

        return test_db_name

    def destroy_test_db(self, old_database_name, verbosity=1, keepdb=False):
        if not keepdb:
            self._destroy_test_db(old_database_name, verbosity=verbosity)

    def _destroy_test_db(self, test_database_name, verbosity=1):
        from .base import _is_local_name

        if _is_local_name(test_database_name):
            if not self.is_in_memory_db(test_database_name):
                if os.path.exists(test_database_name):
                    if verbosity >= 1:
                        self.log(
                            "Destroying test database '%s'..."
                            % test_database_name
                        )
                    os.remove(test_database_name)
                return
            # In-memory: nothing to delete on disk.
            return

        # Remote (Turso HTTP): drop all user tables from the database.
        if verbosity >= 1:
            self.log(
                "Destroying test database tables on '%s'..."
                % test_database_name
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name NOT LIKE 'sqlite_%%' AND name NOT LIKE '_%%'"
            )
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f'DROP TABLE IF EXISTS "{table}"')

    def get_test_db_clone_settings(self, suffix):
        """Return settings dict for a parallel test clone.

        Local mode: returns a suffixed file path (e.g. ``db_1.sqlite3``).
        Remote mode: returns the original settings unchanged — remote
        databases cannot be cloned cheaply, so parallel tests share the
        same database (with table-level isolation per worker).
        """
        from .base import _is_local_name

        orig = self.connection.settings_dict
        name = orig["NAME"]

        if _is_local_name(name) and not self.is_in_memory_db(name):
            root, ext = os.path.splitext(name)
            return {**orig, "NAME": f"{root}_{suffix}{ext}"}

        # In-memory or remote: return unchanged.
        return orig

    def test_db_signature(self):
        """Return a tuple that uniquely identifies a test database."""
        test_name = self._get_test_db_name()
        settings_dict = self.connection.settings_dict
        sig = [settings_dict["NAME"]]
        if self.is_in_memory_db(test_name):
            sig.append(self.connection.alias)
            sig.append("memory")
        else:
            sig.append(test_name)
        sig.append(settings_dict.get("AUTH_TOKEN", ""))
        return tuple(sig)

    def setup_worker_connection(self, connection):
        from .base import _is_local_name

        if _is_local_name(connection.settings_dict.get("NAME", "")):
            from django.db.backends.sqlite3.creation import (
                DatabaseCreation as SQLiteCreation,
            )

            SQLiteCreation.setup_worker_connection(self, connection)
