"""
Database creation for the libSQL/Turso backend.

Since Turso databases are provisioned externally (not created via
Django), this module provides basic test-database creation that
reuses the production connection or creates a separate database.
"""

from django.db.backends.base.creation import BaseDatabaseCreation


class DatabaseCreation(BaseDatabaseCreation):
    def _get_test_db_name(self):
        return self.connection.settings_dict["NAME"]

    def create_test_db(self, verbosity=1, autoclobber=False, keepdb=False):
        if not keepdb:
            self._destroy_test_db(verbosity=verbosity)
        return self.connection.settings_dict["NAME"]

    def destroy_test_db(self, old_database_name, verbosity=1, keepdb=False):
        self._destroy_test_db(verbosity=verbosity)

    def _destroy_test_db(self, verbosity=1):
        with self.connection._nodb_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name NOT LIKE 'sqlite_%%' AND name NOT LIKE '_%%'"
            )
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f'DROP TABLE IF EXISTS "{table}"')

    def test_db_signature(self):
        settings_dict = self.connection.settings_dict
        return (
            self.connection.settings_dict["NAME"],
            settings_dict.get("AUTH_TOKEN", ""),
        )
