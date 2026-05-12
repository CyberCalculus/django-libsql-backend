"""Features for the libSQL/Turso backend — SQLite-compatible, remote HTTP."""

import operator

from django.db.backends.base.features import BaseDatabaseFeatures
from django.utils.functional import cached_property


class DatabaseFeatures(BaseDatabaseFeatures):
    minimum_database_version = (3, 31)
    test_db_allows_multiple_connections = True
    supports_unspecified_pk = True
    supports_timezones = False
    atomic_transactions = False
    can_rollback_ddl = True
    can_create_inline_fk = False
    requires_literal_defaults = True
    can_clone_databases = False
    supports_temporal_subtraction = True
    ignores_table_name_case = True
    supports_cast_with_precision = False
    time_cast_precision = 3
    can_release_savepoints = True
    has_case_insensitive_like = True
    supports_parentheses_in_compound = False
    can_defer_constraint_checks = True
    supports_over_clause = True
    supports_frame_range_fixed_distance = True
    supports_frame_exclusion = True
    supports_aggregate_filter_clause = True
    supports_aggregate_order_by_clause = True
    supports_json_field_contains = False
    supports_update_conflicts = True
    supports_update_conflicts_with_target = True
    order_by_nulls_first = True
    supports_index_on_text_field = True
    supports_stored_generated_columns = True
    supports_virtual_generated_columns = True
    can_alter_table_drop_column = True
    supports_transactions = True
    supports_unlimited_charfield = True
    supports_any_value = True
    supports_aggregate_distinct_multiple_argument = False
    supports_default_keyword_in_insert = False
    insert_test_table_with_defaults = 'INSERT INTO {} ("null") VALUES (1)'

    test_collations = {
        "ci": "nocase",
        "cs": "binary",
        "non_default": "nocase",
    }
    django_test_expected_failures = set()

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
        return True

    can_return_rows_from_bulk_insert = property(
        operator.attrgetter("can_return_columns_from_insert")
    )

    can_return_rows_from_update = property(
        operator.attrgetter("can_return_columns_from_insert")
    )
