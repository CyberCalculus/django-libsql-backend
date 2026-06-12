# Architecture Deep Dive

## Overview

`django-libsql-backend` implements Django's database backend interface (`BaseDatabaseWrapper`) for three transport modes: local `sqlite3`, remote Turso HTTP REST API, and WebSocket (Hrana).

## Connection Modes

### Mode Detection

The `NAME` setting drives mode selection. The `_get_connection_mode()` function in `base.py` returns one of `"local"`, `"http"`, or `"hrana"`.

```
NAME value → _get_connection_mode() → mode string
```

| Mode | Trigger | Transport |
|---|---|---|
| `local` | File path, `:memory:` | Python `sqlite3` module |
| `http` | HTTPS URL, bare hostname, `libsql://` | HTTP REST API (`/v1/execute`, `/v1/batch`) |
| `hrana` | `ws://`, `wss://` | WebSocket via `libsql-client` package |

### Local Mode

Uses Python's built-in `sqlite3` module. On connection:

1. `PRAGMA journal_mode=WAL` — write-ahead logging for concurrency
2. `PRAGMA foreign_keys=ON` — referential integrity enforcement
3. `PRAGMA legacy_alter_table=OFF` — modern ALTER TABLE behavior
4. Registers Django custom SQL functions (date/time, hashing, math, regex)
5. Executes user-supplied `init_command` statements

Full transaction support: `BEGIN`/`COMMIT`/`ROLLBACK`, savepoints, `isolation_level` toggling.

### Remote Mode (HTTP)

Communicates with Turso's HTTP REST API using `urllib.request`. Each request is an independent SQLite connection on the server — no persistent state between requests.

**Endpoints:**
- `/v1/execute` — single statement execution
- `/v1/batch` — multiple statements in one request

**Request format:**
```json
{
  "stmt": {
    "sql": "SELECT * FROM users WHERE id = ?",
    "args": [{"type": "integer", "value": "42"}]
  }
}
```

**Response format:**
```json
{
  "result": {
    "cols": [{"name": "id"}, {"name": "name"}],
    "rows": [[{"type": "integer", "value": "42"}, {"type": "text", "value": "Alice"}]],
    "affected_row_count": 0,
    "last_insert_rowid": null
  }
}
```

### Hrana Mode (WebSocket)

Uses the `libsql-client` package for persistent WebSocket connections. Unlike HTTP mode:

- **Persistent server-side state** — PRAGMAs persist across queries
- **Real transactions** — `BEGIN`/`COMMIT`/`ROLLBACK` work as expected
- **Savepoints** — full savepoint support via the DBAPI layer

Requires the optional dependency:
```bash
pip install django-libsql-backend[hrana]
```

## Transaction Model

### Local Mode

Real SQLite transactions with full ACID guarantees:
- `atomic()` blocks use `BEGIN`/`COMMIT`/`ROLLBACK`
- Savepoints via `SAVEPOINT`/`RELEASE`/`ROLLBACK TO`
- `isolation_level` controls locking behavior

### Remote Mode

Client-side write buffering for best-effort atomicity:

```
atomic() block entered
  ├─ INSERT → buffered (no HTTP request)
  ├─ UPDATE → buffered (no HTTP request)
  ├─ SELECT → auto-flush buffer, then execute
  ├─ cursor.lastrowid → auto-flush buffer
  └─ atomic() block exits
       └─ flush buffer as single batch: BEGIN + PRAGMAs + writes + COMMIT
```

**Key behaviors:**
- **Read-your-writes**: reading inside a transaction auto-flushes buffered writes first
- **lastrowid auto-flush**: Django's ORM reads `lastrowid` after every INSERT, triggering an immediate flush
- **Irreversible flush**: once flushed, writes are committed and cannot be rolled back
- **Rollback after flush**: raises `DatabaseError` with clear message

### Savepoints (Remote)

Savepoints are client-side buffer snapshots:
- `_savepoint(sid)` — records current buffer length
- `_savepoint_rollback(sid)` — truncates buffer to saved length
- `_savepoint_commit(sid)` — no-op (buffer stays as-is)

No `SAVEPOINT` SQL is sent to the server.

## Type Conversion

### Python → Turso

Values are serialized to Turso's typed-value JSON format:

| Python type | Turso type | Example |
|---|---|---|
| `None` | `"null"` | `{"type": "null"}` |
| `bool` | `"integer"` | `{"type": "integer", "value": "1"}` |
| `int` | `"integer"` | `{"type": "integer", "value": "42"}` |
| `float` | `"real"` | `{"type": "real", "value": 3.14}` |
| `bytes` | `"blob"` | `{"type": "blob", "value": "<base64>"}` |
| `Decimal` | `"text"` | `{"type": "text", "value": "3.14"}` |
| `date` | `"text"` | `{"type": "text", "value": "2024-01-15"}` |
| `datetime` | `"text"` | `{"type": "text", "value": "2024-01-15 10:30:00"}` |
| `time` | `"text"` | `{"type": "text", "value": "10:30:00"}` |
| `timedelta` | `"text"` | `{"type": "text", "value": "1 day, 2:00:00"}` |
| `NaN`/`Inf` | `"text"` | `{"type": "text", "value": "NaN"}` |

### Turso → Python

Reverse conversion in `_turso_value_to_py()`:

| Turso type | Python type |
|---|---|
| `"null"` | `None` |
| `"integer"` | `int` |
| `"real"` | `float` |
| `"blob"` | `bytes` (base64 decoded) |
| `"text"` | `str` (with date/time parsing via converters) |

### Placeholder Conversion

Django uses `%s` placeholders. The backend converts these to qmark `?` before sending:

```sql
-- Django format:
SELECT * FROM users WHERE name = %s AND age > %s
-- Converted to:
SELECT * FROM users WHERE name = ? AND age = ?
```

Named parameters (`%(name)s`) are converted to `:name` style.

## SQL Generation

### DatabaseOperations (local)

Uses Django's registered Python functions for date/time operations:

```sql
-- date_extract_sql:
django_date_extract('year', column)

-- datetime_trunc_sql:
django_datetime_trunc('month', column, 'UTC', 'UTC')
```

### RemoteDatabaseOperations (remote)

Replaces Django custom functions with native SQLite expressions:

```sql
-- date_extract_sql:
CAST(strftime('%Y', column) AS integer)

-- datetime_trunc_sql:
strftime('%Y-%m-01 00:00:00', column)

-- date_trunc_sql (quarter):
strftime('%Y', column) || '-' ||
SUBSTR('0' || ((CAST(strftime('%m', column) AS integer) - 1)
/ 3 * 3 + 1), -2) || '-01'
```

### SQL Rewrites

Before sending to remote servers, some SQL functions are rewritten:

| Original | Rewritten |
|---|---|
| `COT(x)` | `(1.0 / TAN(x))` |
| `SIGN(x)` | `CASE WHEN x > 0 THEN 1 WHEN x < 0 THEN -1 ELSE 0 END` |

## Schema Editor

The `DatabaseSchemaEditor` proxies Django's built-in SQLite schema editor with one override:

- **Local mode**: full FK constraint management (disable before ALTER, re-enable after)
- **Remote mode**: skips FK toggling (PRAGMAs don't persist across requests)

## Introspection

The `DatabaseIntrospection` fully proxies Django's built-in SQLite introspection. All `sqlite_master` queries and `PRAGMA` introspection work correctly over HTTP.

## Error Handling

HTTP errors are mapped to Django database exceptions:

| HTTP Status | Django Exception | Condition |
|---|---|---|
| 400 | `IntegrityError` | UNIQUE/FOREIGN KEY violation |
| 400 | `DataError` | Other bad request |
| 401 | `OperationalError` | Invalid auth token |
| 403 | `OperationalError` | Insufficient permissions |
| 404 | `OperationalError` | Database not found |
| 429 | `OperationalError` | Rate limit exceeded |
| 5xx | `OperationalError` | Server error |

SQL-level errors are also detected in response bodies:

| Error pattern | Django Exception |
|---|---|
| `BUSY`, `LOCKED` | `OperationalError` |
| `SYNTAX ERROR`, `NO SUCH TABLE/COLUMN` | `ProgrammingError` |
