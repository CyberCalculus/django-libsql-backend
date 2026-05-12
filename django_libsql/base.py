"""
Django database backend for libSQL/Turso.

Supports both remote Turso/libSQL databases via HTTP REST API and local
SQLite files. Connection type is auto-detected from the NAME setting.

Remote (Turso HTTP):
    Each HTTP request is an independent SQLite connection — there is no
    persistent session. Transactions, savepoints, and connection-stateful
    PRAGMAs behave accordingly.

Local (sqlite3 file):
    Uses Python's built-in sqlite3 module. Full transaction support, WAL
    mode, and persistent PRAGMA state within a connection.
"""

import json
import sqlite3
import urllib.request
import urllib.error
import re
from collections.abc import Mapping

from sqlite3 import dbapi2 as Database

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.base.base import BaseDatabaseWrapper
from django.utils.asyncio import async_unsafe

from .features import DatabaseFeatures
from .operations import DatabaseOperations
from .client import DatabaseClient
from .creation import DatabaseCreation
from .introspection import DatabaseIntrospection
from .schema import DatabaseSchemaEditor

FORMAT_QMARK_REGEX = re.compile(r"(?<!%)%s")


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
    # Bare hostname like "db.turso.io" — has dots, no path separators,
    # and does not match any known file extension above.
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
        return {"type": "real", "value": value}
    if isinstance(value, (bytes, memoryview, bytearray)):
        import base64
        return {
            "type": "blob",
            "value": base64.b64encode(bytes(value)).decode("ascii"),
        }
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


def _build_turso_args(params):
    """Build Turso-format args list from Python params."""
    if params is None:
        return None
    if isinstance(params, Mapping):
        raise NotImplementedError("Named parameters not supported; use qmark style")
    return [_py_value_to_turso_type(p) for p in params]


class TursoHTTPConnection:
    """Minimal HTTP connection to Turso's REST API."""

    def __init__(self, base_url, auth_token, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

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
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"Turso HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Turso connection error: {e.reason}") from e


class TursoCursor:
    """DB-API 2.0 compatible cursor that calls Turso HTTP API."""

    def __init__(self, connection):
        self.connection = connection
        self._rows = []
        self._columns = ()
        self._index = 0
        self.rowcount = -1
        self.lastrowid = None
        self.description = None
        self._closed = False

    def _convert_query(self, query):
        """Convert Django format-style %s to qmark ? for Turso."""
        return FORMAT_QMARK_REGEX.sub("?", query).replace("%%", "%")

    def execute(self, sql, params=None):
        if self._closed:
            raise RuntimeError("Cursor is closed")
        sql = self._convert_query(sql)
        payload = {"stmt": {"sql": sql}}
        if params:
            payload["stmt"]["args"] = _build_turso_args(params)

        data = self.connection.request("/v1/execute", payload)
        result = data.get("result", {})

        self._columns = tuple(c["name"] for c in result.get("cols", []))
        self._rows = [
            tuple(_turso_value_to_py(cell) for cell in row)
            for row in result.get("rows", [])
        ]
        self._index = 0
        self.rowcount = result.get("affected_row_count", -1)
        self.lastrowid = result.get("last_insert_rowid")

        if self._columns:
            self.description = [
                (name, None, None, None, None, None, None) for name in self._columns
            ]
        return self

    def executemany(self, sql, param_list):
        """Use batch endpoint for multiple parameter sets."""
        if self._closed:
            raise RuntimeError("Cursor is closed")
        sql = self._convert_query(sql)
        steps = [
            {"stmt": {"sql": sql, "args": _build_turso_args(p) or []}}
            for p in param_list
        ]
        data = self.connection.request("/v1/batch", {"batch": {"steps": steps}})
        result = data.get("result", {})
        self.rowcount = 0
        for step_result in result.get("step_results", []):
            self.rowcount += step_result.get("affected_row_count", 0)
        self.lastrowid = None
        self._rows = []
        self._columns = ()
        self._index = 0
        self.description = None
        return self

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

    def _convert_query(self, query):
        return FORMAT_QMARK_REGEX.sub("?", query).replace("%%", "%")

    def execute(self, sql, params=None):
        if self._closed:
            raise RuntimeError("Cursor is closed")
        sql = self._convert_query(sql)
        if params:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return self

    def executemany(self, sql, param_list):
        if self._closed:
            raise RuntimeError("Cursor is closed")
        sql = self._convert_query(sql)
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
        "CharField": "varchar(%(max_length)s)",
        "DateField": "date",
        "DateTimeField": "datetime",
        "DecimalField": "decimal",
        "DurationField": "bigint",
        "FileField": "varchar(%(max_length)s)",
        "FilePathField": "varchar(%(max_length)s)",
        "FloatField": "real",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "IPAddressField": "char(15)",
        "GenericIPAddressField": "char(39)",
        "JSONField": "text",
        "PositiveBigIntegerField": "bigint unsigned",
        "PositiveIntegerField": "integer unsigned",
        "PositiveSmallIntegerField": "smallint unsigned",
        "SlugField": "varchar(%(max_length)s)",
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

    SchemaEditorClass = DatabaseSchemaEditor
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations

    def __init__(self, settings_dict, alias="default"):
        super().__init__(settings_dict, alias)
        self._http_connection = None

    def get_connection_params(self):
        settings_dict = self.settings_dict
        name = settings_dict.get("NAME")
        if not name:
            # Django's _nodb_cursor passes NAME=None for test DB teardown.
            # Default to in-memory local SQLite.
            return {"is_local": True, "filepath": ":memory:"}
        if _is_local_name(name):
            # sqlite3.connect() handles relative paths natively — no
            # need to resolve against BASE_DIR.
            return {"is_local": True, "filepath": name}
        # Remote: convert libsql:// → https://, add https:// to bare hostnames
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
            conn = sqlite3.connect(
                conn_params["filepath"],
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        return TursoHTTPConnection(
            base_url=conn_params["url"],
            auth_token=conn_params["auth_token"],
            timeout=conn_params["timeout"],
        )

    def init_connection_state(self):
        """Initialize database connection settings."""
        pass

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
                self.connection.request("/v1/execute", {"stmt": {"sql": "SELECT 1"}})
            else:
                self.connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _close(self):
        if self.connection is not None:
            if isinstance(self.connection, TursoHTTPConnection):
                # HTTP connections are stateless — nothing to close.
                pass
            else:
                self.connection.close()

    def _set_autocommit(self, autocommit):
        # For local SQLite, setting isolation_level=None enables autocommit
        # mode, while setting it to 'DEFERRED' starts implicit transactions.
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            if autocommit:
                self.connection.isolation_level = None
            else:
                self.connection.isolation_level = "DEFERRED"

    def _start_transaction_under_autocommit(self):
        """Start an explicit transaction while staying in autocommit mode."""
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            self.connection.execute("BEGIN")

    def _commit(self):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            self.connection.commit()

    def _rollback(self):
        if self.connection is not None and not isinstance(
            self.connection, TursoHTTPConnection
        ):
            self.connection.rollback()

    def disable_constraint_checking(self):
        """Disable FK checks via PRAGMA. Returns True if successfully disabled."""
        with self.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys")
            was_enabled = bool(cursor.fetchone()[0])
            if was_enabled:
                cursor.execute("PRAGMA foreign_keys = OFF")
            return was_enabled

    def enable_constraint_checking(self):
        """Re-enable FK checks."""
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
                        'PRAGMA foreign_key_check("%s")' % table_name
                    ).fetchall()
                    for table_name in table_names
                )
            for (table_name, rowid, ref_table, fk_idx) in violations:
                raise RuntimeError(
                    f"Foreign key violation in table '{table_name}', "
                    f"rowid={rowid}"
                )

    def is_in_memory_db(self):
        return False

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
