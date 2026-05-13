"""Features for the libSQL/Turso backend — SQLite-compatible, remote HTTP."""

import operator

from django.db.backends.base.features import BaseDatabaseFeatures
from django.utils.functional import cached_property


class DatabaseFeatures(BaseDatabaseFeatures):
    minimum_database_version = (3, 31)
    @cached_property
    def test_db_allows_multiple_connections(self):
        return not self._is_local
    supports_unspecified_pk = True
    supports_timezones = False
    @cached_property
    def atomic_transactions(self):
        return not self._is_local
    @cached_property
    def can_rollback_ddl(self):
        return self._is_local
    can_create_inline_fk = False
    requires_literal_defaults = True
    @cached_property
    def can_clone_databases(self):
        return self._is_local
    supports_temporal_subtraction = True
    ignores_table_name_case = True
    supports_cast_with_precision = False
    time_cast_precision = 3
    @cached_property
    def uses_savepoints(self):
        return self._is_local
    @cached_property
    def can_release_savepoints(self):
        return self._is_local
    has_case_insensitive_like = True
    supports_parentheses_in_compound = False
    @cached_property
    def can_defer_constraint_checks(self):
        return self._is_local
    supports_over_clause = True
    supports_frame_range_fixed_distance = True
    supports_frame_exclusion = True
    supports_aggregate_filter_clause = True
    @cached_property
    def supports_aggregate_order_by_clause(self):
        if self._is_local:
            return self.connection.get_database_version() >= (3, 44, 0)
        return True
    supports_json_field_contains = False
    supports_update_conflicts = True
    supports_update_conflicts_with_target = True
    order_by_nulls_first = True
    supports_index_on_text_field = True
    supports_stored_generated_columns = True
    supports_virtual_generated_columns = True
    @cached_property
    def can_alter_table_drop_column(self):
        if self._is_local:
            return self.connection.get_database_version() >= (3, 35, 5)
        return True
    @cached_property
    def supports_transactions(self):
        return self._is_local
    supports_unlimited_charfield = True
    supports_any_value = True
    supports_aggregate_distinct_multiple_argument = False
    supports_default_keyword_in_insert = False
    insert_test_table_with_defaults = 'INSERT INTO {} ("null") VALUES (1)'

    test_collations = {
        "ci": "nocase",
        "cs": "binary",
        "non_default": "nocase",
        "virtual": "nocase",
    }
    django_test_expected_failures = set()
    django_test_skips = {
        "schema.tests.SchemaTests.test_alter_field_default_does_not_perform_queries",
    }

    @property
    def _is_local(self):
        """Return True if connection is local SQLite, False for remote Turso HTTP."""
        name = self.connection.settings_dict.get("NAME")
        if not name:
            return True  # empty or None = in-memory = local
        if name.startswith(("http://", "https://", "libsql://")):
            return False
        if name.startswith(("/", ".")):
            return True
        if name.endswith((".sqlite3", ".db", ".sqlite", ".s3db", ".sl3")):
            return True
        if "." in name and "/" not in name and "\\" not in name:
            return False  # bare hostname like "my-db.turso.io"
        return True

    @cached_property
    def max_query_params(self):
        if self._is_local:
            import sqlite3

            return sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER
        return 32766  # Modern SQLite on Turso servers

    @cached_property
    def introspected_field_types(self):
        return {
            **super().introspected_field_types,
            "BigAutoField": "AutoField",
            "DurationField": "BigIntegerField",
            "GenericIPAddressField": "CharField",
            "SmallAutoField": "AutoField",
        }

    @cached_property
    def supports_json_field(self):
        return True

    can_introspect_json_field = property(operator.attrgetter("supports_json_field"))
    has_json_object_function = property(operator.attrgetter("supports_json_field"))

    @cached_property
    def can_return_columns_from_insert(self):
        if self._is_local:
            return self.connection.get_database_version() >= (3, 35, 0)
        return False

    can_return_rows_from_bulk_insert = property(
        operator.attrgetter("can_return_columns_from_insert")
    )

    can_return_rows_from_update = property(
        operator.attrgetter("can_return_columns_from_insert")
    )
