"""
Django database backend for libSQL/Turso.

Supports both remote Turso/libSQL databases via HTTP REST API and local
SQLite files. Connection type is auto-detected from the NAME setting.

Remote (Turso HTTP):
    Each HTTP request is an independent SQLite connection — there is no
    persistent session. Transactions are implemented via statement buffering:
    writes are queued during an atomic() block and sent as a single batch
    (wrapped in BEGIN/COMMIT) on commit. Reads within a transaction execute
    immediately against the server and will NOT see buffered writes from the
    same transaction. Accessing ``lastrowid`` after a buffered INSERT triggers
    an auto-flush that commits the current buffer — after this point, rollback
    cannot undo the flushed statements.

Local (sqlite3 file):
    Uses Python's built-in sqlite3 module. Full transaction support, WAL
    mode, persistent PRAGMA state, and all 30+ Django custom SQL functions
    registered on the connection.
"""

import datetime
import decimal
import json
import logging
import math
import re
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Mapping

from sqlite3 import dbapi2 as Database

from django.core.exceptions import ImproperlyConfigured
from django.db import (
    DataError,
    DatabaseError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
)
from django.db.backends.base.base import BaseDatabaseWrapper, RAN_DB_VERSION_CHECK
from django.utils.asyncio import async_unsafe
from django.utils.dateparse import parse_date, parse_datetime, parse_time


def decoder(conv_func):
    """Convert bytestrings from Python's sqlite3 interface to a regular string."""
    return lambda s: conv_func(s.decode())


def adapt_date(val):
    return val.isoformat()


def adapt_datetime(val):
    return val.isoformat(" ")


Database.register_converter("bool", b"1".__eq__)
Database.register_converter("date", decoder(parse_date))
Database.register_converter("time", decoder(parse_time))
Database.register_converter("datetime", decoder(parse_datetime))
Database.register_converter("timestamp", decoder(parse_datetime))

Database.register_adapter(decimal.Decimal, str)
Database.register_adapter(datetime.date, adapt_date)
Database.register_adapter(datetime.datetime, adapt_datetime)

from .features import DatabaseFeatures
from .operations import DatabaseOperations, RemoteDatabaseOperations
from .client import DatabaseClient
from .creation import DatabaseCreation
from .introspection import DatabaseIntrospection
from .schema import DatabaseSchemaEditor

FORMAT_QMARK_REGEX = re.compile(r"(?<!%)%s")

# SQL prefixes that indicate a write statement (cached for buffer-or-execute).
_WRITE_PREFIXES = (
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
    "REPLACE",
)


def _strip_sql_comments(sql):
    """Remove SQL comments from a statement for write-detection purposes."""
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    return sql


def _is_write_statement(sql):
    """Return True if *sql* is a mutating statement that should be buffered."""
    cleaned = _strip_sql_comments(sql.strip()).strip()
    return cleaned.upper().startswith(_WRITE_PREFIXES)


def _is_local_name(name):
    """Return True if NAME looks like a local file path, False if remote URL."""
    if not name:
        return False
    if name.startswith(("http://", "https://", "libsql://")):
        return False
    if name.startswith(("/", ".")):
        return True
    if name.endswith((".sqlite3", ".db", ".sqlite", ".s3db", ".sl3")):
        return True
    if "." in name and "/" not in name and "\\" not in name:
        return False
    return True


def _py_value_to_turso_type(value):
    """Convert a Python value to a Turso typed-value dict."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {"type": "text", "value": "NaN"}
        if math.isinf(value):
            return {"type": "text", "value": "Infinity" if value > 0 else "-Infinity"}
        return {"type": "real", "value": value}
    if isinstance(value, (bytes, memoryview, bytearray)):
        import base64
        # bytearray is explicitly listed since it is not a subclass of bytes
        # or memoryview, ensuring it is handled before the catch-all.
        return {
            "type": "blob",
            "value": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, decimal.Decimal):
        return {"type": "text", "value": str(value)}
    if isinstance(value, datetime.date):
        return {"type": "text", "value": value.isoformat()}
    if isinstance(value, datetime.datetime):
        return {"type": "text", "value": value.isoformat(" ")}
    if isinstance(value, datetime.time):
        return {"type": "text", "value": value.isoformat()}
    if isinstance(value, datetime.timedelta):
        return {"type": "text", "value": str(value)}
    logger = logging.getLogger("django.db.backends")
    logger.warning(
        "Unsupported type %s passed as parameter, converting to string",
        type(value).__name__,
    )
    return {"type": "text", "value": str(value)}


def _turso_value_to_py(cell):
    """Convert a Turso typed-value dict to a Python value."""
    ctype = cell.get("type", "text")
    value = cell.get("value")
    if ctype == "null" or value is None or (ctype == "text" and value == "NULL"):
        return None if ctype == "null" else value
    if ctype == "integer":
        return int(value)
    if ctype == "real":
        return float(value)
    if ctype == "blob":
        import base64

        return base64.b64decode(value)
    return value


def _build_turso_args(params, param_names=None):
    """Build Turso-format args list from Python params."""
    if params is None:
        return None
    if isinstance(params, Mapping):
        if param_names is None:
            param_names = list(params)
        return [_py_value_to_turso_type(params[name]) for name in param_names]
    return [_py_value_to_turso_type(p) for p in params]


def _get_varchar_column(data):
    """Return varchar type for CharField, handling optional max_length."""
    if data.get("max_length") is None:
        return "varchar"
    return "varchar(%(max_length)s)" % data


class TursoHTTPConnection:
    """Minimal HTTP connection to Turso's REST API with transaction buffering."""

    _CONNECTION_PRAGMAS = [
        "PRAGMA foreign_keys = ON",
        "PRAGMA legacy_alter_table = OFF",
    ]

    def __init__(self, base_url, auth_token, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        # Transaction buffering state.
        self._transaction_level = 0
        self._transaction_buffer = []
        self._flushed = False  # True if any part of this txn was committed
        self._savepoint_buffer_positions = {}  # sid -> buffer length at savepoint

    # -- transaction API --------------------------------------------------

    @property
    def in_transaction(self):
        return self._transaction_level > 0

    def enter_transaction(self):
        """Begin buffering writes for a new transaction (nestable)."""
        if self._transaction_level == 0:
            self._flushed = False
        self._transaction_level += 1

    def commit_transaction(self):
        """Flush buffered writes with BEGIN/COMMIT on outermost commit."""
        if self._transaction_level == 1 and self._transaction_buffer:
            self._flush_with_begin_commit()
        if self._transaction_level > 0:
            self._transaction_level -= 1
            if self._transaction_level == 0:
                self._flushed = False

    def rollback_transaction(self):
        """Discard buffered writes on outermost rollback.

        If any buffered writes were auto-flushed (to serve a read or obtain
        lastrowid), they are already committed and rollback is impossible.
        The transaction state is reset anyway so the connection is usable,
        and a DatabaseError is raised to inform the caller.
        """
        flushed = self._flushed
        if self._transaction_level == 1:
            self._transaction_buffer = []
        if self._transaction_level > 0:
            self._transaction_level -= 1
            if self._transaction_level == 0:
                self._flushed = False
        if flushed:
            raise DatabaseError(
                "Cannot roll back transaction: buffered statements were "
                "auto-flushed (to obtain the last inserted row ID or to "
                "serve a read query) and are already committed on the "
                "remote server."
            )

    def buffer_statement(self, sql, args):
        """Append a write statement to the transaction buffer."""
        self._transaction_buffer.append(
            {"stmt": {"sql": sql, "args": list(args) if args else []}}
        )

    def _flush_with_begin_commit(self):
        """Send buffered statements wrapped in BEGIN/COMMIT as a batch.

        Returns the full batch response dict, or None if the buffer is empty.
        After this call the buffer is cleared and all statements are committed.
        Sets _flushed so rollback can detect the irreversible commit.
        """
        if not self._transaction_buffer:
            return None
        steps = [{"stmt": {"sql": "BEGIN"}}]
        steps.extend(
            {"stmt": {"sql": pragma}} for pragma in self._CONNECTION_PRAGMAS
        )
        steps.extend(self._transaction_buffer)
        steps.append({"stmt": {"sql": "COMMIT"}})
        try:
            response = self.request("/v1/batch", {"batch": {"steps": steps}})
            self._flushed = True
            return response
        finally:
            self._transaction_buffer = []

    def flush_for_lastrowid(self):
        """Flush the buffer and return the last INSERT's rowid.

        This commits the transaction. Caller must understand that after this
        point a rollback cannot undo the flushed statements.
        """
        if not self._transaction_buffer:
            return None
        response = self._flush_with_begin_commit()
        if response is None:
            return None
        result = response.get("result", {})
        step_results = result.get("step_results", [])
        # Find the last step that returned a last_insert_rowid.
        for step in reversed(step_results):
            rowid = step.get("last_insert_rowid")
            if rowid is not None:
                return rowid
        return None

    # -- HTTP transport ---------------------------------------------------

    def request(self, path, body):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth_token}",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            try:
                return json.loads(resp.read())
            except json.JSONDecodeError:
                raise OperationalError(
                    "Turso returned malformed JSON response"
                )
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            self._raise_http_error(e.code, body_text)
        except urllib.error.URLError as e:
            raise OperationalError(
                f"Turso connection error: {e.reason}"
            ) from e

    @staticmethod
    def _raise_http_error(code, body):
        """Map Turso HTTP errors to Django database exceptions."""
        upper = body.upper()

        # Check for transient/lock errors regardless of HTTP code.
        if "BUSY" in upper or "LOCKED" in upper or "DATABASE IS LOCKED" in upper:
            raise OperationalError(f"Turso: {body}")

        # Check for syntax/schema errors regardless of HTTP code.
        if "SYNTAX ERROR" in upper or "NO SUCH TABLE" in upper or "NO SUCH COLUMN" in upper:
            raise ProgrammingError(f"Turso: {body}")

        if code == 400:
            if "UNIQUE" in upper or "FOREIGN KEY" in upper:
                raise IntegrityError(f"Turso: {body}")
            raise DataError(f"Turso HTTP 400: {body}")

        if code == 401:
            raise OperationalError(
                "Turso authentication failed: invalid or missing AUTH_TOKEN. "
                "Check your DATABASES setting 'AUTH_TOKEN'."
            )
        if code == 403:
            raise OperationalError(
                "Turso authorization denied: the AUTH_TOKEN does not have "
                "permission to access this database."
            )
        if code == 404:
            raise OperationalError(f"Turso database not found ({code}): {body}")

        if code == 429:
            raise OperationalError(f"Turso rate limit exceeded ({code}): {body}")

        if code in (500, 502, 503, 504):
            raise OperationalError(f"Turso server error ({code}): {body}")

        raise DatabaseError(f"Turso HTTP {code}: {body}")


class TursoCursor:
    """DB-API 2.0 compatible cursor that calls Turso HTTP API.

    Supports transaction buffering: when the connection is inside an
    atomic() block, write statements (INSERT/UPDATE/DELETE/CREATE/DROP/ALTER)
    are buffered and sent as a single batch on commit. Reads execute
    immediately against the server.
    """

    def __init__(self, connection):
        self.connection = connection
        self._rows = []
        self._columns = ()
        self._index = 0
        self._rowcount = -1
        self._lastrowid = None
        self._description = None
        self._closed = False
        self._buffered = False

    @staticmethod
    def _convert_query(query, *, param_names=None):
        """Convert Django format-style %s or pyformat-style %(name)s."""
        if param_names is None:
            return FORMAT_QMARK_REGEX.sub("?", query).replace("%%", "%")
        else:
            return query % {name: f":{name}" for name in param_names}

    def _process_response(self, data):
        """Populate cursor state from a Turso /v1/execute response."""
        result = data.get("result", {})
        self._columns = tuple(c["name"] for c in result.get("cols", []))
        self._rows = [
            tuple(_turso_value_to_py(cell) for cell in row)
            for row in result.get("rows", [])
        ]
        self._index = 0
        self._rowcount = result.get("affected_row_count", -1)
        if "last_insert_rowid" in result:
            self._lastrowid = result["last_insert_rowid"]
        self._description = None
        if self._columns:
            self._description = [
                (name, None, None, None, None, None, None)
                for name in self._columns
            ]
        return self

    def execute(self, sql, params=None):
        if self._closed:
            raise RuntimeError("Cursor is closed")
        param_names = list(params) if isinstance(params, Mapping) else None
        sql = self._convert_query(sql, param_names=param_names)
        self._buffered = False

        # In a remote transaction: buffer writes, execute reads immediately.
        # RETURNING statements must execute immediately so that
        # fetch_returned_rows() can capture results -- they cannot be
        # buffered. This means RETURNING inserts auto-commit inside
        # transactions, similar to lastrowid access triggering auto-flush.
        if (self.connection.in_transaction
                and _is_write_statement(sql)
                and "RETURNING" not in sql.upper()):
            args = _build_turso_args(params, param_names)
            self.connection.buffer_statement(sql, args)
            self._buffered = True
            self._rows = []
            self._columns = ()
            self._rowcount = 0
            self._lastrowid = None
            self._description = None
            return self

        # Auto-flush buffered writes before a read so the read sees any
        # prior writes from the same transaction. After flushing, those
        # writes are committed and cannot be rolled back.
        if (self.connection.in_transaction and
                self.connection._transaction_buffer):
            self.connection._flush_with_begin_commit()

        payload = {"stmt": {"sql": sql}}
        if params:
            payload["stmt"]["args"] = _build_turso_args(params, param_names)
        data = self.connection.request("/v1/execute", payload)
        return self._process_response(data)

    def executemany(self, sql, param_list):
        if self._closed:
            raise RuntimeError("Cursor is closed")

        # Peek at the first element to detect pyformat (Mapping) params.
        from itertools import tee

        peekable, param_list = tee(iter(param_list))
        if (params := next(peekable, None)) and isinstance(params, Mapping):
            param_names = list(params)
        else:
            param_names = None
        sql = self._convert_query(sql, param_names=param_names)
        self._buffered = False

        # In a remote transaction: buffer each parameter set.
        # RETURNING statements execute immediately (not buffered) to
        # preserve result availability.
        if (self.connection.in_transaction
                and _is_write_statement(sql)
                and "RETURNING" not in sql.upper()):
            count = 0
            for params in param_list:
                args = _build_turso_args(params, param_names)
                self.connection.buffer_statement(sql, args)
                count += 1
            self._buffered = True
            self._rowcount = count
            self._lastrowid = None
            self._rows = []
            self._columns = ()
            self._description = None
            return self

        # Auto-flush buffered writes before sending to server so results
        # include prior writes from the same transaction.
        if (self.connection.in_transaction and
                self.connection._transaction_buffer):
            self.connection._flush_with_begin_commit()

        steps = [
            {"stmt": {"sql": sql, "args": _build_turso_args(p, param_names) or []}}
            for p in param_list
        ]
        data = self.connection.request("/v1/batch", {"batch": {"steps": steps}})
        result = data.get("result", {})
        self._rowcount = 0
        for step_result in result.get("step_results", []):
            self._rowcount += step_result.get("affected_row_count", 0)
        self._lastrowid = None
        self._rows = []
        self._columns = ()
        self._description = None
        return self

    @property
    def lastrowid(self):
        """Auto-flush buffered writes when the ORM needs the rowid.

        This is the escape hatch for the stateless-HTTP constraint:
        Django's ORM calls ``cursor.lastrowid`` immediately after every
        INSERT to populate the model's PK.  If the INSERT was buffered
        (no HTTP request yet), we must flush the buffer now — which
        commits it.  After this point the transaction is effectively
        committed and rollback has no effect.
        """
        if self._buffered and self.connection.in_transaction:
            rowid = self.connection.flush_for_lastrowid()
            self._buffered = False
            if rowid is not None:
                self._lastrowid = rowid
        return self._lastrowid

    @lastrowid.setter
    def lastrowid(self, value):
        self._lastrowid = value

    @property
    def rowcount(self):
        return self._rowcount

    @rowcount.setter
    def rowcount(self, value):
        self._rowcount = value

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, value):
        self._description = value

    def fetchone(self):
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows

    def fetchmany(self, size=None):
        if size is None:
            size = 1
        rows = self._rows[self._index:self._index + size]
        self._index += len(rows)
        return rows

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class LocalSQLiteCursor:
    """DB-API 2.0 compatible cursor wrapping a local sqlite3.Cursor."""

    def __init__(self, sqlite_conn):
        self._cursor = sqlite_conn.cursor()
        self._closed = False

    @staticmethod
    def _convert_query(query, *, param_names=None):
        """Convert Django format-style %s or pyformat-style %(name)s."""
        if param_names is None:
            return FORMAT_QMARK_REGEX.sub("?", query).replace("%%", "%")
        else:
            return query % {name: f":{name}" for name in param_names}

    def execute(self, sql, params=None):
        if self._closed:
            raise RuntimeError("Cursor is closed")
        param_names = list(params) if isinstance(params, Mapping) else None
        sql = self._convert_query(sql, param_names=param_names)
        if params:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return self

    def executemany(self, sql, param_list):
        if self._closed:
            raise RuntimeError("Cursor is closed")

        # Peek at the first element to detect pyformat (Mapping) params.
        from itertools import tee

        peekable, param_list = tee(iter(param_list))
        if (params := next(peekable, None)) and isinstance(params, Mapping):
            param_names = list(params)
        else:
            param_names = None
        sql = self._convert_query(sql, param_names=param_names)
        self._cursor.executemany(sql, param_list)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size)

    def close(self):
        self._closed = True
        self._cursor.close()

    @property
    def closed(self):
        return self._closed

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def description(self):
        return self._cursor.description

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = "libsql"
    display_name = "libSQL (Turso)"
    Database = Database

    data_types = {
        "AutoField": "integer",
        "BigAutoField": "integer",
        "BinaryField": "BLOB",
        "BooleanField": "bool",
        "CharField": _get_varchar_column,
        "DateField": "date",
        "DateTimeField": "datetime",
        "DecimalField": "decimal",
        "DurationField": "bigint",
        "FileField": _get_varchar_column,
        "FilePathField": _get_varchar_column,
        "FloatField": "real",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "IPAddressField": "char(15)",
        "GenericIPAddressField": "char(39)",
        "JSONField": "text",
        "PositiveBigIntegerField": "bigint unsigned",
        "PositiveIntegerField": "integer unsigned",
        "PositiveSmallIntegerField": "smallint unsigned",
        "SlugField": _get_varchar_column,
        "SmallAutoField": "integer",
        "SmallIntegerField": "smallint",
        "TextField": "text",
        "TimeField": "time",
        "UUIDField": "char(32)",
    }
    data_type_check_constraints = {
        "PositiveBigIntegerField": '"%(column)s" >= 0',
        "JSONField": '(JSON_VALID("%(column)s") OR "%(column)s" IS NULL)',
        "PositiveIntegerField": '"%(column)s" >= 0',
        "PositiveSmallIntegerField": '"%(column)s" >= 0',
    }
    data_types_suffix = {
        "AutoField": "AUTOINCREMENT",
        "BigAutoField": "AUTOINCREMENT",
        "SmallAutoField": "AUTOINCREMENT",
    }
    operators = {
        "exact": "= %s",
        "iexact": "LIKE %s ESCAPE '\\'",
        "contains": "LIKE %s ESCAPE '\\'",
        "icontains": "LIKE %s ESCAPE '\\'",
        "regex": "REGEXP %s",
        "iregex": "REGEXP '(?i)' || %s",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE %s ESCAPE '\\'",
        "endswith": "LIKE %s ESCAPE '\\'",
        "istartswith": "LIKE %s ESCAPE '\\'",
        "iendswith": "LIKE %s ESCAPE '\\'",
    }
    pattern_esc = (
        r"REPLACE(REPLACE(REPLACE({}, '\', '\\'), '%%', '\%%'), '_', '\_')"
    )
    pattern_ops = {
        "contains": r"LIKE '%%' || {} || '%%' ESCAPE '\'",
        "icontains": r"LIKE '%%' || UPPER({}) || '%%' ESCAPE '\'",
        "startswith": r"LIKE {} || '%%' ESCAPE '\'",
        "istartswith": r"LIKE UPPER({}) || '%%' ESCAPE '\'",
        "endswith": r"LIKE '%%' || {} ESCAPE '\'",
        "iendswith": r"LIKE '%%' || UPPER({}) ESCAPE '\'",
    }

    transaction_modes = frozenset(["DEFERRED", "EXCLUSIVE", "IMMEDIATE"])

    SchemaEditorClass = DatabaseSchemaEditor
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations  # default; overridden per-instance for remote

    def __init__(self, settings_dict, alias="default"):
        self._http_connection = None
        self._transaction_mode = None
        # Detect mode early so ops_class is correct before Base init.
        name = settings_dict.get("NAME") or ""
        if name and not _is_local_name(name):
            self.ops_class = RemoteDatabaseOperations
        super().__init__(settings_dict, alias)

    def get_connection_params(self):
        settings_dict = self.settings_dict
        name = settings_dict.get("NAME")
        if not name:
            return {"is_local": True, "filepath": ":memory:"}
        if _is_local_name(name):
            return {"is_local": True, "filepath": name}
        if name.startswith("libsql://"):
            url = name.replace("libsql://", "https://", 1)
        elif "://" in name:
            url = name
        else:
            url = f"https://{name}"
        return {
            "is_local": False,
            "url": url,
            "auth_token": settings_dict.get("AUTH_TOKEN", ""),
            "timeout": settings_dict.get("OPTIONS", {}).get("timeout", 30),
        }

    @async_unsafe
    def get_new_connection(self, conn_params):
        if conn_params["is_local"]:
            options = self.settings_dict.get("OPTIONS", {})
            conn = sqlite3.connect(
                conn_params["filepath"],
                detect_types=Database.PARSE_DECLTYPES | Database.PARSE_COLNAMES,
                check_same_thread=False,
                uri=True,
                **{
                    k: v
                    for k, v in options.items()
                    if k not in ("timeout", "init_command", "transaction_mode")
                },
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA legacy_alter_table=OFF")

            # Register Django's 30+ custom SQL functions.
            from .functions import register_functions

            register_functions(conn)

            # Run user-supplied init commands.
            init_command = options.get("init_command", "")
            for cmd in init_command.split(";"):
                if cmd := cmd.strip():
                    conn.execute(cmd)

            # Store transaction mode for _start_transaction_under_autocommit.
            txn_mode = options.get("transaction_mode")
            if txn_mode:
                txn_mode = txn_mode.upper()
                if txn_mode not in self.transaction_modes:
                    allowed = ", ".join(
                        f"{m!r}" for m in sorted(self.transaction_modes)
                    )
                    raise ImproperlyConfigured(
                        f"settings.DATABASES[{self.alias!r}]['OPTIONS']"
                        f"['transaction_mode'] is improperly configured to "
                        f"{txn_mode!r}. Use one of {allowed}, or None."
                    )
            self._transaction_mode = txn_mode

            return conn
        return TursoHTTPConnection(
            base_url=conn_params["url"],
            auth_token=conn_params["auth_token"],
            timeout=conn_params["timeout"],
        )

    def init_connection_state(self):
        if self.alias not in RAN_DB_VERSION_CHECK:
            self.check_database_version_supported()
            RAN_DB_VERSION_CHECK.add(self.alias)
        # Remote mode: critical PRAGMAs (foreign_keys, legacy_alter_table)
        # are included at the beginning of every batch request in
        # _flush_with_begin_commit() since PRAGMAs do not persist between
        # stateless HTTP requests on Turso.

    @async_unsafe
    def create_cursor(self, name=None):
        if self.connection is None:
            raise RuntimeError("No connection established")
        if isinstance(self.connection, TursoHTTPConnection):
            return TursoCursor(self.connection)
        return LocalSQLiteCursor(self.connection)

    def is_usable(self):
        if self.connection is None:
            return False
        try:
            if isinstance(self.connection, TursoHTTPConnection):
                self.connection.request(
                    "/v1/execute", {"stmt": {"sql": "SELECT 1"}}
                )
            else:
                self.connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    # -- close / memory protection ----------------------------------------

    def _close(self):
        if self.connection is not None:
            if isinstance(self.connection, TursoHTTPConnection):
                if self.connection._transaction_buffer:
                    logger = logging.getLogger("django.db.backends")
                    logger.warning(
                        "%d uncommitted write(s) discarded on connection close.",
                        len(self.connection._transaction_buffer),
                    )
            else:
                if not self.is_in_memory_db():
                    with self.wrap_database_errors:
                        self.connection.close()

    def is_in_memory_db(self):
        if self.connection is not None and isinstance(
            self.connection, TursoHTTPConnection
        ):
            return False
        return self.creation.is_in_memory_db(
            self.settings_dict.get("NAME") or ""
        )

    # -- transaction management -------------------------------------------

    def _set_autocommit(self, autocommit):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            if autocommit:
                self.connection.isolation_level = None
            else:
                self.connection.isolation_level = "DEFERRED"
        # Remote mode: autocommit toggling is a no-op. Transaction boundaries
        # are managed by _start_transaction_under_autocommit / _commit / _rollback.

    def _start_transaction_under_autocommit(self):
        if self.connection is not None:
            if isinstance(self.connection, TursoHTTPConnection):
                self.connection.enter_transaction()
            elif self._transaction_mode:
                self.connection.execute(f"BEGIN {self._transaction_mode}")
            else:
                self.connection.execute("BEGIN")

    def _commit(self):
        if self.connection is not None:
            if isinstance(self.connection, TursoHTTPConnection):
                self.connection.commit_transaction()
            else:
                self.connection.commit()

    def _rollback(self):
        if self.connection is not None:
            if isinstance(self.connection, TursoHTTPConnection):
                self.connection.rollback_transaction()
            else:
                self.connection.rollback()

    def _savepoint_allowed(self):
        # sqlite3 has a bug where savepoints are created outside atomic
        # blocks when isolation_level is not None. The Django SQLite
        # backend works around this by only allowing savepoints inside
        # atomic blocks. For remote mode, savepoints are no-ops and
        # nesting is tracked via _transaction_level — always safe.
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            return self.in_atomic_block
        return True

    def _savepoint(self, sid):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            self.connection.execute(self.ops.savepoint_create_sql(sid))
        elif isinstance(self.connection, TursoHTTPConnection):
            # Remote mode: track buffer length so _savepoint_rollback
            # can discard writes buffered within this savepoint.
            self.connection._savepoint_buffer_positions[sid] = len(
                self.connection._transaction_buffer
            )

    def _savepoint_commit(self, sid):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            self.connection.execute(self.ops.savepoint_commit_sql(sid))

    def _savepoint_rollback(self, sid):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            self.connection.execute(self.ops.savepoint_rollback_sql(sid))
        elif isinstance(self.connection, TursoHTTPConnection):
            # Remote: discard writes that were buffered during this savepoint.
            saved = self.connection._savepoint_buffer_positions.pop(sid, None)
            if saved is not None:
                del self.connection._transaction_buffer[saved:]

    # -- constraint checking ----------------------------------------------

    def disable_constraint_checking(self):
        with self.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys")
            was_enabled = bool(cursor.fetchone()[0])
            if was_enabled:
                cursor.execute("PRAGMA foreign_keys = OFF")
            return not was_enabled

    def enable_constraint_checking(self):
        with self.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = ON")

    def check_constraints(self, table_names=None):
        with self.cursor() as cursor:
            if table_names is None:
                violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
            else:
                from itertools import chain

                violations = chain.from_iterable(
                    cursor.execute(
                        "PRAGMA foreign_key_check(%s)"
                        % self.ops.quote_name(table_name)
                    ).fetchall()
                    for table_name in table_names
                )
            for table_name, rowid, ref_table, fk_idx in violations:
                raise IntegrityError(
                    "Foreign key violation in table '%s', rowid=%s, "
                    "referenced table '%s', fk index %s"
                    % (table_name, rowid, ref_table, fk_idx)
                )

    # -- version ----------------------------------------------------------

    def get_database_version(self):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            return sqlite3.sqlite_version_info[:3]
        with self.cursor() as cursor:
            cursor.execute("SELECT sqlite_version()")
            row = cursor.fetchone()
            if row:
                parts = row[0].split(".")
                return tuple(int(p) for p in parts[:3])
        return (3, 0, 0)
