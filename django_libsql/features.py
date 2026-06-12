"""Features for the libSQL/Turso backend -- SQLite-compatible, remote HTTP."""

from __future__ import annotations

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
        # Both modes: Django manages transactions explicitly via atomic()
        # blocks. Remote mode buffers writes client-side; local mode uses
        # sqlite3's native transaction support. Setting this to False
        # matches Django's built-in SQLite backend behavior.
        return False

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
    django_test_expected_failures = {
        "expressions.tests.FTimeDeltaTests.test_mixed_comparisons1",
    }
    django_test_skips = {
        "schema.tests.SchemaTests.test_alter_field_default_does_not_perform_queries",
        "model_fields.test_decimalfield."
        "DecimalFieldTests.test_fetch_from_db_without_float_rounding",
        "backends.base.test_base.ExecuteWrapperTests.test_wrapper_debug",
    }

    @property
    def _is_local(self):
        """Return True if connection is local SQLite, False for remote Turso HTTP."""
        from .base import _get_connection_mode

        name = self.connection.settings_dict.get("NAME") or ""
        return _get_connection_mode(name) == "local"

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
        if self._is_local:
            # Runtime check: verify JSON1 extension is available (matches
            # Django's sqlite3/features.py behavior). Some SQLite builds
            # ship without the JSON1 extension.
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute("SELECT json('{}')")
                return True
            except Exception:
                return False
        # Remote mode: Turso servers ship SQLite 3.45+ with JSON1 built in.
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

    can_return_rows_from_update = property(operator.attrgetter("can_return_columns_from_insert"))
