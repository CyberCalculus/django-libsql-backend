# Known Limitations

## Remote Mode (Turso HTTP)

These limitations apply only to remote (Turso HTTP) mode. Local mode and Hrana (WebSocket) mode have full SQLite support.

### Stateless Connection

Each HTTP request creates a new SQLite connection. This means:

- **PRAGMAs don't persist** between requests
- **DDL auto-commits** — `ALTER TABLE`, `CREATE TABLE`, `DROP TABLE` cannot be rolled back
- **FK constraint deferral** is not possible — needs persistent PRAGMA state

### Transaction Semantics

Remote mode uses client-side write buffering:

```python
with transaction.atomic():
    obj = MyModel.objects.create(name="foo")  # buffered
    # obj.pk is available (auto-flush triggered by lastrowid access)
    exists = MyModel.objects.filter(name="foo").exists()
    # exists may be False — the CREATE was buffered, not yet sent
```

**Workarounds:**
- Rely on Django's ORM patterns (model save + lastrowid) for single-object operations
- Use `SELECT` after `INSERT` only if you've triggered a flush (e.g., via `lastrowid`)
- For complex transactional logic, consider using local mode or Hrana mode

### Schema Migrations

`_remake_table` (used by `ALTER TABLE` operations) may fail for tables with foreign keys due to the stateless PRAGMA issue. Common operations that work:

- Creating new tables
- Adding columns to existing tables
- Creating indexes

Operations that may fail:

- Renaming columns on tables with FK references
- Dropping columns on tables with FK references
- Changing column types on FK-referenced tables

### DDL Rollback

Each DDL statement (`CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`) auto-commits immediately. If a migration partially fails, you may need to manually fix the schema state.

## Hrana Mode (WebSocket)

Hrana mode has fewer limitations than HTTP mode, but requires the `libsql-client` package:

```bash
pip install django-libsql-backend[hrana]
```

**Advantages over HTTP mode:**
- Persistent server-side state
- Real transactions with `BEGIN`/`COMMIT`/`ROLLBACK`
- Full savepoint support
- PRAGMA persistence across queries

**Limitations:**
- Not all Turso databases support WebSocket — check your database configuration
- Requires the `libsql-client` package (adds `aiohttp` dependency)
- WebSocket connections may be less reliable than HTTP in some network environments

## Unavailable SQL Functions

These Django ORM functions use Python-registered SQL functions that are not available on remote servers (HTTP and Hrana modes):

### Hash Functions

- `MD5`
- `SHA1`
- `SHA224`
- `SHA256`
- `SHA384`
- `SHA512`

**Workaround:** Compute hashes in Python before passing to queries, or use native SQLite expressions if your Turso server has the extension loaded.

### String Functions

- `LPad`
- `RPad`
- `Repeat`
- `Reverse`

**Workaround:** Use native SQLite `SUBSTR`, `REPLACE`, or `||` concatenation.

### Math Functions

- `Cot` — rewritten to `(1.0 / TAN(x))` automatically
- `Sign` — rewritten to `CASE WHEN x > 0 THEN 1 ...` automatically
- `BitXor` — raises `NotSupportedError` (no native equivalent)

### Aggregate Functions

- `StdDev` — raises `NotSupportedError`
- `Variance` — raises `NotSupportedError`

**Workaround:** Compute standard deviation and variance in Python.

## Timezone Handling

SQLite has no native timezone support. In remote mode:

- All date/time SQL uses `strftime`/`date`/`time` without timezone conversion
- Results are **correct** when the database stores UTC (Django's default)
- Queries involving non-UTC timezone conversion may produce incorrect results

**Recommendation:** Keep `USE_TZ = True` and store datetimes in UTC.

## Duration Arithmetic

In remote mode, `combine_duration_expression` with two `DurationField` operands (e.g., `F('a') + F('b')` where both are `DurationField`) uses `datetime()` which interprets the first integer as a Julian day number, producing a date string instead of an integer sum.

**Workaround:** Use `*` and `/` operators for duration math, or compute in Python.

## Type Limitations

### NaN/Inf Floats

`NaN` and `Infinity` are stored as text strings (`"NaN"`, `"Infinity"`, `"-Infinity"`). Round-trip returns strings instead of `float('nan')`/`float('inf')`.

### UUID Storage

UUIDs are stored as `char(32)` (hex string without dashes). No native UUID type, validation, or generation at the database level.

### JSON Fields

JSON fields are stored as `text` with a `JSON_VALID()` check constraint. No JSON indexing, no JSON operators (`->`, `->>`, `@>`).

### Boolean Fields

Booleans are stored as integers (`0`/`1`). SQLite uses dynamic typing — `bool` is affinity-based, not enforced.

## Test Database Isolation

### Local Mode

- Test database is a separate file (e.g., `dev_test.sqlite3`)
- Supports parallel test execution via file cloning

### Remote Mode

- Reuses the production database (Turso databases cannot be created on the fly)
- Tables are dropped between test runs
- Configure `TEST["NAME"]` to point at a dedicated test database if needed

## Performance Considerations

### Remote Mode Latency

Each query incurs HTTP round-trip latency. For high-throughput workloads:

- Use batch operations (`executemany()`) when possible
- Minimize the number of queries per request
- Consider connection pooling at the application level

### Local Mode

Full SQLite performance with WAL journal mode. Suitable for development and testing.

## Django Version Compatibility

### Tested Versions

- Django 4.2 LTS
- Django 5.0
- Django 5.1
- Django 6.0

### Python Versions

- Python 3.10+
- Tested on 3.10, 3.11, 3.12, 3.13
