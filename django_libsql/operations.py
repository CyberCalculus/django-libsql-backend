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
from django.db.models import CompositePrimaryKey
from django.db.models.expressions import Col
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.functional import cached_property


class DatabaseOperations(BaseDatabaseOperations):
    """SQL generation for local sqlite3 mode (uses Django-registered functions)."""

    cast_char_field_without_max_length = "text"
    cast_data_types = {
        "DateField": "TEXT",
        "DateTimeField": "TEXT",
    }
    explain_prefix = "EXPLAIN QUERY PLAN"
    jsonfield_datatype_values = frozenset(["null", "false", "true"])

    def bulk_batch_size(self, fields, objs):
        django_fields = []
        for field in fields:
            if isinstance(field, CompositePrimaryKey):
                django_fields.extend(field.get_cols())
            else:
                django_fields.append(field)
        max_params = self.connection.features.max_query_params or 999
        if django_fields:
            return max(max_params // len(django_fields), 1)
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
        if not params:
            return sql
        if isinstance(params, (list, tuple)):
            quoted = []
            for p in params:
                if p is None:
                    quoted.append("NULL")
                elif isinstance(p, (int, float)):
                    quoted.append(str(p))
                else:
                    quoted.append(repr(str(p)))
            return sql % tuple(quoted)
        return sql % {k: repr(str(v)) if v is not None else "NULL" for k, v in params.items()}

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

    @cached_property
    def force_group_by(self):
        if self.connection.get_database_version() < (3, 39, 0):
            return ["GROUP BY TRUE"]
        return []

    def format_json_path_numeric_index(self, index):
        if isinstance(index, int) and index < 0:
            return "[#%s]" % index
        return super().format_json_path_numeric_index(index)


class RemoteDatabaseOperations(DatabaseOperations):
    """
    SQL generation for remote (Turso HTTP) mode.

    Django's SQLite backend registers Python functions (django_date_extract,
    django_date_trunc, django_datetime_extract, etc.) on the sqlite3 connection
    object. On Turso's remote HTTP API, we cannot register Python functions —
    each request is a stateless SQLite connection on the server.

    This class overrides the SQL generators to use only native SQLite built-in
    functions (strftime, date, time, julianday) instead of Django custom
    functions. The generated SQL is valid on any standard SQLite 3.31+ server.

    Timezone conversion is NOT performed at the SQL level — SQLite has no
    timezone support. This means datetime lookups are correct when:
      - USE_TZ=False (naive datetimes)
      - USE_TZ=True with the database storing UTC (Django's default)
    Incorrect results may occur if the database stores non-UTC naive datetimes
    and the query involves timezone conversion.

    Limitations in remote mode:
      - REGEXP may not work on all Turso/libSQL servers (requires the
        regexp extension to be loaded on the server side).
      - STDDEV_POP, STDDEV_SAMP, VAR_POP, VAR_SAMP aggregate functions are
        not available. Use of StdDev/Variance aggregates will raise
        NotSupportedError.
    """

    def check_expression_support(self, expression):
        bad_aggregates = (models.StdDev, models.Variance)
        if isinstance(expression, bad_aggregates):
            raise NotSupportedError(
                "StdDev and Variance aggregates are not supported in remote "
                "(Turso HTTP) mode. These SQL functions (STDDEV_SAMP, VAR_SAMP, "
                "STDDEV_POP, VAR_POP) are not available on all Turso/libSQL "
                "servers."
            )
        super().check_expression_support(expression)

    # strftime format strings for date_extract lookup types.
    # Lookups with custom formulas are handled directly in the *extract methods.
    _DATE_EXTRACT_FORMATS = {
        "year": "%Y",
        "month": "%m",
        "day": "%d",
        "iso_year": "%G",
    }

    _DATETIME_EXTRACT_FORMATS = {
        **_DATE_EXTRACT_FORMATS,
        "hour": "%H",
        "minute": "%M",
    }

    def date_extract_sql(self, lookup_type, sql, params):
        lt = lookup_type.lower()
        if lt == "quarter":
            return (
                "(CAST(strftime('%%m', %s) AS integer) + 2) / 3" % sql,
                params,
            )
        if lt == "week_day":
            return (
                "CAST(strftime('%%w', %s) AS integer) + 1" % sql,
                params,
            )
        if lt == "iso_week_day":
            return (
                "CAST(strftime('%%u', %s) AS integer)" % sql,
                params,
            )
        if lt == "week":
            return (
                "CAST((strftime('%%j', %s, '-3 days', 'weekday 4') - 1) / 7 + 1 "
                "AS integer)" % sql,
                params,
            )
        fmt = self._DATE_EXTRACT_FORMATS.get(lt)
        if fmt:
            return (
                "CAST(strftime('%s', %s) AS integer)" % (fmt, sql),
                params,
            )
        raise NotSupportedError(
            "Date extract '%s' is not supported in remote mode." % lt
        )

    def datetime_extract_sql(self, lookup_type, sql, params, tzname):
        lt = lookup_type.lower()
        if lt == "quarter":
            return (
                "(CAST(strftime('%%m', %s) AS integer) + 2) / 3" % sql,
                params,
            )
        if lt == "week_day":
            return (
                "CAST(strftime('%%w', %s) AS integer) + 1" % sql,
                params,
            )
        if lt == "iso_week_day":
            return (
                "CAST(strftime('%%u', %s) AS integer)" % sql,
                params,
            )
        if lt == "week":
            return (
                "CAST((strftime('%%j', %s, '-3 days', 'weekday 4') - 1) / 7 + 1 "
                "AS integer)" % sql,
                params,
            )
        if lt == "second":
            return (
                "CAST(strftime('%%S', %s) AS integer)" % sql,
                params,
            )
        fmt = self._DATETIME_EXTRACT_FORMATS.get(lt)
        if fmt:
            return (
                "CAST(strftime('%s', %s) AS integer)" % (fmt, sql),
                params,
            )
        raise NotSupportedError(
            "Datetime extract '%s' is not supported in remote mode." % lt
        )

    def time_extract_sql(self, lookup_type, sql, params):
        lt = lookup_type.lower()
        if lt == "second":
            return (
                "CAST(strftime('%%S', %s) AS integer)" % sql,
                params,
            )
        fmt = {"hour": "%H", "minute": "%M"}.get(lt)
        if fmt:
            return (
                "CAST(strftime('%s', %s) AS integer)" % (fmt, sql),
                params,
            )
        raise NotSupportedError(
            "Time extract '%s' is not supported in remote mode." % lt
        )

    def date_trunc_sql(self, lookup_type, sql, params, tzname=None):
        lt = lookup_type.lower()
        if lt == "year":
            return "strftime('%%Y-01-01', %s)" % sql, params
        elif lt == "month":
            return "strftime('%%Y-%%m-01', %s)" % sql, params
        elif lt == "day":
            return "strftime('%%Y-%%m-%%d', %s)" % sql, params
        elif lt == "quarter":
            # Quarter start: month = ((m - 1) / 3) * 3 + 1
            return (
                "strftime('%%Y', %(col)s) || '-' || "
                "SUBSTR('0' || ((CAST(strftime('%%m', %(col)s) AS integer) - 1) "
                "/ 3 * 3 + 1), -2) || '-01'"
            ) % {"col": sql}, params
        elif lt == "week":
            # Monday of the current week: date(col, '-N days')
            # N = (strftime('%w', col) + 6) %% 7
            return (
                "date(%(col)s, '-' || "
                "((CAST(strftime('%%w', %(col)s) AS integer) + 6) %% 7) || "
                "' days')"
            ) % {"col": sql}, params
        raise NotSupportedError(
            "Date trunc '%s' is not supported in remote mode." % lt
        )

    def datetime_trunc_sql(self, lookup_type, sql, params, tzname=None):
        lt = lookup_type.lower()
        fmts = {
            "year": "%%Y-01-01 00:00:00",
            "quarter": None,  # handled separately
            "month": "%%Y-%%m-01 00:00:00",
            "week": None,     # handled separately
            "day": "%%Y-%%m-%%d 00:00:00",
            "hour": "%%Y-%%m-%%d %%H:00:00",
            "minute": "%%Y-%%m-%%d %%H:%%M:00",
            "second": "%%Y-%%m-%%d %%H:%%M:%%S",
        }
        if lt == "quarter":
            return (
                "strftime('%%Y', %(col)s) || '-' || "
                "SUBSTR('0' || ((CAST(strftime('%%m', %(col)s) AS integer) - 1) "
                "/ 3 * 3 + 1), -2) || '-01 00:00:00'"
            ) % {"col": sql}, params
        if lt == "week":
            return (
                "strftime('%%Y-%%m-%%d 00:00:00', "
                "date(%(col)s, '-' || "
                "((CAST(strftime('%%w', %(col)s) AS integer) + 6) %% 7) || "
                "' days'))"
            ) % {"col": sql}, params
        fmt = fmts.get(lt)
        if fmt:
            return "strftime('%s', %s)" % (fmt, sql), params
        raise NotSupportedError(
            "Datetime trunc '%s' is not supported in remote mode." % lt
        )

    def time_trunc_sql(self, lookup_type, sql, params, tzname=None):
        lt = lookup_type.lower()
        fmts = {
            "hour": "%%H:00:00",
            "minute": "%%H:%%M:00",
            "second": "%%H:%%M:%%S",
        }
        fmt = fmts.get(lt)
        if fmt:
            return "strftime('%s', %s)" % (fmt, sql), params
        raise NotSupportedError(
            "Time trunc '%s' is not supported in remote mode." % lt
        )

    def datetime_cast_date_sql(self, sql, params, tzname):
        return "date(%s)" % sql, params

    def datetime_cast_time_sql(self, sql, params, tzname):
        return "time(%s)" % sql, params

    def subtract_temporals(self, internal_type, lhs, rhs):
        lhs_sql, lhs_params = lhs
        rhs_sql, rhs_params = rhs
        params = (*lhs_params, *rhs_params)
        if internal_type == "TimeField":
            # (julianday('2000-01-01 ' || lhs) - julianday('2000-01-01 ' || rhs))
            # * 86400000000 → microseconds
            return (
                "(julianday('2000-01-01 ' || %s) - "
                "julianday('2000-01-01 ' || %s)) * 86400000000"
            ) % (lhs_sql, rhs_sql), params
        # timestamp_diff: (julianday(lhs) - julianday(rhs)) * 86400000000
        return (
            "(julianday(%s) - julianday(%s)) * 86400000000"
        ) % (lhs_sql, rhs_sql), params

    def combine_expression(self, connector, sub_expressions):
        if connector == "#":
            raise NotSupportedError(
                "BITXOR is not available in remote (Turso HTTP) mode."
            )
        return super().combine_expression(connector, sub_expressions)

    def combine_duration_expression(self, connector, sub_expressions):
        # django_format_dtdelta doesn't exist on remote servers.
        # Provide a fallback using native SQLite datetime() for +/- and raw
        # arithmetic for */.
        #
        # Limitation: timedelta + timedelta (both DurationField values) uses the
        # same code path. datetime() interprets the first integer as a Julian
        # day number, producing a date string instead of an integer. Use
        # explicit integer arithmetic (F('a') + F('b') as a regular expression,
        # not a duration expression) when combining two DurationField values.
        if connector not in ["+", "-", "*", "/"]:
            raise DatabaseError("Invalid connector for timedelta: %s." % connector)
        if len(sub_expressions) > 2:
            raise ValueError("Too many params for timedelta operations.")
        lhs, rhs = sub_expressions
        if connector == "+":
            return (
                "datetime(%s, '+' || (%s) / 1000000.0 || ' seconds')"
            ) % (lhs, rhs)
        elif connector == "-":
            return (
                "datetime(%s, '-' || (%s) / 1000000.0 || ' seconds')"
            ) % (lhs, rhs)
        elif connector == "*":
            return "(%s) * (%s)" % (lhs, rhs)
        else:
            return "(%s) / (%s)" % (lhs, rhs)
