"""Operations for the libSQL/Turso backend — SQLite-compatible SQL generation."""

import datetime
import decimal
import uuid
from functools import lru_cache
from itertools import chain

from django.conf import settings
from django.core.exceptions import FieldError
from django.db import DatabaseError, NotSupportedError, models
from django.db.backends.base.operations import BaseDatabaseOperations
from django.db.models.constants import OnConflict
from django.db.models.expressions import Col
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.functional import cached_property


class DatabaseOperations(BaseDatabaseOperations):
    cast_char_field_without_max_length = "text"
    cast_data_types = {
        "DateField": "TEXT",
        "DateTimeField": "TEXT",
    }
    explain_prefix = "EXPLAIN QUERY PLAN"
    jsonfield_datatype_values = frozenset(["null", "false", "true"])

    def bulk_batch_size(self, fields, objs):
        max_params = 999
        if len(fields) == 1:
            return 500
        elif len(fields) > 1:
            return max_params // len(fields)
        else:
            return len(objs)

    def check_expression_support(self, expression):
        bad_fields = (models.DateField, models.DateTimeField, models.TimeField)
        bad_aggregates = (models.Sum, models.Avg, models.Variance, models.StdDev)
        if isinstance(expression, bad_aggregates):
            for expr in expression.get_source_expressions():
                try:
                    output_field = expr.output_field
                except (AttributeError, FieldError):
                    pass
                else:
                    if isinstance(output_field, bad_fields):
                        raise NotSupportedError(
                            "Sum, Avg, StdDev, Variance aggregations on date/time "
                            "fields not supported on SQLite (stored as text)."
                        )
        if (
            isinstance(expression, models.Aggregate)
            and expression.distinct
            and len(expression.source_expressions) > 1
        ):
            raise NotSupportedError(
                "SQLite doesn't support DISTINCT on aggregate functions "
                "accepting multiple arguments."
            )

    def date_extract_sql(self, lookup_type, sql, params):
        return f"django_date_extract(%s, {sql})", (lookup_type.lower(), *params)

    def format_for_duration_arithmetic(self, sql):
        return sql

    def date_trunc_sql(self, lookup_type, sql, params, tzname=None):
        return f"django_date_trunc(%s, {sql}, %s, %s)", (
            lookup_type.lower(),
            *params,
            *self._convert_tznames_to_sql(tzname),
        )

    def time_trunc_sql(self, lookup_type, sql, params, tzname=None):
        return f"django_time_trunc(%s, {sql}, %s, %s)", (
            lookup_type.lower(),
            *params,
            *self._convert_tznames_to_sql(tzname),
        )

    def _convert_tznames_to_sql(self, tzname):
        if tzname and settings.USE_TZ:
            return tzname, self.connection.timezone_name
        return None, None

    def datetime_cast_date_sql(self, sql, params, tzname):
        return f"django_datetime_cast_date({sql}, %s, %s)", (
            *params,
            *self._convert_tznames_to_sql(tzname),
        )

    def datetime_cast_time_sql(self, sql, params, tzname):
        return f"django_datetime_cast_time({sql}, %s, %s)", (
            *params,
            *self._convert_tznames_to_sql(tzname),
        )

    def datetime_extract_sql(self, lookup_type, sql, params, tzname):
        return f"django_datetime_extract(%s, {sql}, %s, %s)", (
            lookup_type.lower(),
            *params,
            *self._convert_tznames_to_sql(tzname),
        )

    def datetime_trunc_sql(self, lookup_type, sql, params, tzname):
        return f"django_datetime_trunc(%s, {sql}, %s, %s)", (
            lookup_type.lower(),
            *params,
            *self._convert_tznames_to_sql(tzname),
        )

    def time_extract_sql(self, lookup_type, sql, params):
        return f"django_time_extract(%s, {sql})", (lookup_type.lower(), *params)

    def pk_default_value(self):
        return "NULL"

    def quote_name(self, name):
        if name.startswith('"') and name.endswith('"'):
            return name
        return '"%s"' % name

    def no_limit_value(self):
        return -1

    def last_executed_query(self, cursor, sql, params):
        return sql % params if params else sql

    def __references_graph(self, table_name):
        query = """
        WITH tables AS (
            SELECT %s name
            UNION
            SELECT sqlite_master.name
            FROM sqlite_master
            JOIN tables ON (sql REGEXP %s || tables.name || %s)
        ) SELECT name FROM tables;
        """
        params = (
            table_name,
            r'(?i)\s+references\s+("|\')?',
            r'("|\')?\s*\(',
        )
        with self.connection.cursor() as cursor:
            results = cursor.execute(query, params)
            return [row[0] for row in results.fetchall()]

    @cached_property
    def _references_graph(self):
        return lru_cache(maxsize=512)(self.__references_graph)

    def sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
        if tables and allow_cascade:
            tables = set(
                chain.from_iterable(self._references_graph(table) for table in tables)
            )
        sql = [
            "%s %s %s;"
            % (
                style.SQL_KEYWORD("DELETE"),
                style.SQL_KEYWORD("FROM"),
                style.SQL_FIELD(self.quote_name(table)),
            )
            for table in tables
        ]
        if reset_sequences:
            sequences = [{"table": table} for table in tables]
            sql.extend(self.sequence_reset_by_name_sql(style, sequences))
        return sql

    def sequence_reset_by_name_sql(self, style, sequences):
        if not sequences:
            return []
        return [
            "%s %s %s %s = 0 %s %s %s (%s);"
            % (
                style.SQL_KEYWORD("UPDATE"),
                style.SQL_TABLE(self.quote_name("sqlite_sequence")),
                style.SQL_KEYWORD("SET"),
                style.SQL_FIELD(self.quote_name("seq")),
                style.SQL_KEYWORD("WHERE"),
                style.SQL_FIELD(self.quote_name("name")),
                style.SQL_KEYWORD("IN"),
                ", ".join(
                    ["'%s'" % seq["table"] for seq in sequences]
                ),
            ),
        ]

    def adapt_datetimefield_value(self, value):
        if value is None:
            return None
        if hasattr(value, "resolve_expression"):
            return value
        if timezone.is_aware(value):
            if settings.USE_TZ:
                value = timezone.make_naive(value, self.connection.timezone)
            else:
                raise ValueError(
                    "SQLite does not support timezone-aware datetimes when "
                    "USE_TZ is False."
                )
        return str(value)

    def adapt_timefield_value(self, value):
        if value is None:
            return None
        if hasattr(value, "resolve_expression"):
            return value
        if timezone.is_aware(value):
            raise ValueError("SQLite does not support timezone-aware times.")
        return str(value)

    def get_db_converters(self, expression):
        converters = super().get_db_converters(expression)
        internal_type = expression.output_field.get_internal_type()
        if internal_type == "DateTimeField":
            converters.append(self.convert_datetimefield_value)
        elif internal_type == "DateField":
            converters.append(self.convert_datefield_value)
        elif internal_type == "TimeField":
            converters.append(self.convert_timefield_value)
        elif internal_type == "DecimalField":
            converters.append(self.get_decimalfield_converter(expression))
        elif internal_type == "UUIDField":
            converters.append(self.convert_uuidfield_value)
        elif internal_type == "BooleanField":
            converters.append(self.convert_booleanfield_value)
        return converters

    def convert_datetimefield_value(self, value, expression, connection):
        if value is not None:
            if not isinstance(value, datetime.datetime):
                value = parse_datetime(value)
            if settings.USE_TZ and not timezone.is_aware(value):
                value = timezone.make_aware(value, self.connection.timezone)
        return value

    def convert_datefield_value(self, value, expression, connection):
        if value is not None:
            if not isinstance(value, datetime.date):
                value = parse_date(value)
        return value

    def convert_timefield_value(self, value, expression, connection):
        if value is not None:
            if not isinstance(value, datetime.time):
                value = parse_time(value)
        return value

    def get_decimalfield_converter(self, expression):
        create_decimal = decimal.Context(prec=15).create_decimal_from_float
        if isinstance(expression, Col):
            quantize_value = decimal.Decimal(1).scaleb(
                -expression.output_field.decimal_places
            )

            def converter(value, expression, connection):
                if value is not None:
                    return create_decimal(value).quantize(
                        quantize_value, context=expression.output_field.context
                    )

        else:

            def converter(value, expression, connection):
                if value is not None:
                    return create_decimal(value)

        return converter

    def convert_uuidfield_value(self, value, expression, connection):
        if value is not None:
            value = uuid.UUID(value)
        return value

    def convert_booleanfield_value(self, value, expression, connection):
        return bool(value) if value in (1, 0) else value

    def combine_expression(self, connector, sub_expressions):
        if connector == "^":
            return "POWER(%s)" % ",".join(sub_expressions)
        elif connector == "#":
            return "BITXOR(%s)" % ",".join(sub_expressions)
        return super().combine_expression(connector, sub_expressions)

    def combine_duration_expression(self, connector, sub_expressions):
        if connector not in ["+", "-", "*", "/"]:
            raise DatabaseError("Invalid connector for timedelta: %s." % connector)
        fn_params = ["'%s'" % connector, *sub_expressions]
        if len(fn_params) > 3:
            raise ValueError("Too many params for timedelta operations.")
        return "django_format_dtdelta(%s)" % ", ".join(fn_params)

    def integer_field_range(self, internal_type):
        if internal_type in [
            "PositiveBigIntegerField",
            "PositiveIntegerField",
            "PositiveSmallIntegerField",
        ]:
            return (0, 9223372036854775807)
        return (-9223372036854775808, 9223372036854775807)

    def subtract_temporals(self, internal_type, lhs, rhs):
        lhs_sql, lhs_params = lhs
        rhs_sql, rhs_params = rhs
        params = (*lhs_params, *rhs_params)
        if internal_type == "TimeField":
            return "django_time_diff(%s, %s)" % (lhs_sql, rhs_sql), params
        return "django_timestamp_diff(%s, %s)" % (lhs_sql, rhs_sql), params

    def insert_statement(self, on_conflict=None):
        if on_conflict == OnConflict.IGNORE:
            return "INSERT OR IGNORE INTO"
        return super().insert_statement(on_conflict=on_conflict)

    def on_conflict_suffix_sql(self, fields, on_conflict, update_fields, unique_fields):
        if (
            on_conflict == OnConflict.UPDATE
            and self.connection.features.supports_update_conflicts_with_target
        ):
            return "ON CONFLICT(%s) DO UPDATE SET %s" % (
                ", ".join(map(self.quote_name, unique_fields)),
                ", ".join(
                    [
                        f"{field} = EXCLUDED.{field}"
                        for field in map(self.quote_name, update_fields)
                    ]
                ),
            )
        return super().on_conflict_suffix_sql(
            fields,
            on_conflict,
            update_fields,
            unique_fields,
        )

    def force_group_by(self):
        return []
