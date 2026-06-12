# Troubleshooting

## Common Errors

### "Turso HTTP 401: authentication failed"

Your auth token is invalid or expired.

**Fix:** Generate a new token:
```bash
turso db tokens create <db-name>
```

Update your `AUTH_TOKEN` setting.

### "Turso authorization denied (403)"

The auth token doesn't have permission to access this database.

**Fix:** Ensure the token was created for the correct database:
```bash
turso db tokens create <db-name>
```

### "Turso connection error: timed out"

Network connectivity issue or timeout too short.

**Fix:** Increase the timeout:
```python
"OPTIONS": {"timeout": 60}
```

Check your network connectivity:
```bash
curl -I https://your-db.turso.io
```

### "Turso database not found (404)"

The database URL is incorrect or the database doesn't exist.

**Fix:** Verify the database exists:
```bash
turso db list
```

Check your `NAME` setting.

### "Turso rate limit exceeded (429)"

Too many requests. Turso has rate limits on HTTP API requests.

**Fix:**
- Reduce query frequency
- Use batch operations (`executemany()`) for bulk inserts
- Upgrade your Turso plan for higher limits

### "RuntimeError: No connection established"

The HTTP connection hasn't been initialized.

**Fix:** Verify your database settings:
```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "https://your-db.turso.io",  # Check this
        "AUTH_TOKEN": "your-token",           # And this
    }
}
```

### "ImproperlyConfigured: transaction_mode is improperly configured"

Invalid `transaction_mode` value.

**Fix:** Use one of: `"DEFERRED"`, `"EXCLUSIVE"`, `"IMMEDIATE"`, or `None`.

```python
"OPTIONS": {"transaction_mode": "DEFERRED"}
```

### "DatabaseError: Cannot roll back transaction"

In remote mode, a rollback was attempted after buffered writes were auto-flushed.

**Cause:** This happens when:
1. A write is buffered (inside `atomic()`)
2. A read or `lastrowid` access triggers auto-flush
3. Rollback is attempted after the flush

**Workaround:** Structure code to avoid read-after-write patterns in remote transactions, or accept that writes are committed once flushed.

### "migrations fail with 'no such table: django_migrations'"

The database hasn't been migrated yet.

**Fix:**
```bash
python manage.py migrate
```

### "OperationalError: Turso server error (5xx)"

Temporary server-side issue.

**Fix:** Retry the operation. If persistent, check Turso status page.

### "NotSupportedError: StdDev and Variance aggregates are not supported"

These aggregate functions require SQL extensions not available on all Turso servers.

**Workaround:** Compute standard deviation and variance in Python.

### "NotSupportedError: MD5 is not supported in remote mode"

Hash functions require Python registration which is unavailable on remote servers.

**Workaround:** Compute hashes in Python before passing to queries, or use native SQLite expressions.

### "ProgrammingError: Turso: SYNTAX ERROR"

SQL syntax error in your query.

**Fix:** Check your SQL syntax. Common issues:
- Missing quotes around string values
- Incorrect column names
- Using MySQL/PostgreSQL-specific syntax

### "ProgrammingError: Turso: NO SUCH TABLE"

The table doesn't exist in the database.

**Fix:** Run migrations first:
```bash
python manage.py migrate
```

### "ProgrammingError: Turso: NO SUCH COLUMN"

The column doesn't exist in the table.

**Fix:** Check your model field names and ensure migrations are up to date.

## Hrana Mode Errors

### "ImproperlyConfigured: Hrana connections require 'libsql-client'"

The `libsql-client` package is not installed.

**Fix:**
```bash
pip install django-libsql-backend[hrana]
```

### "400, message='Invalid response status', url='wss://...'"

The WebSocket connection was rejected. This usually means:
- Your Turso database doesn't support WebSocket/Hrana mode
- The URL is incorrect

**Fix:** Use HTTP mode instead (convert `wss://` to `https://`), or check your database configuration.

### "sqlite3.OperationalError: WebSocket connection failed"

WebSocket connection failed due to network or authentication issues.

**Fix:**
- Check your network connectivity
- Verify the AUTH_TOKEN is valid
- Ensure the database URL is correct

## Debugging

### Enable SQL Logging

```python
LOGGING = {
    "version": 1,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django.db.backends": {
            "level": "DEBUG",
            "handlers": ["console"],
        },
    },
}
```

### Check Connection Mode

```python
from django.db import connection

print(f"Mode: {connection._connection_mode}")
print(f"Vendor: {connection.vendor}")
print(f"Database version: {connection.get_database_version()}")
```

### Test Database Connectivity

```python
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SELECT 1")
    print(f"Connected: {cursor.fetchone()}")
```

### Inspect Transaction State (Remote)

```python
from django.db import connection

conn = connection.connection
print(f"In transaction: {conn.in_transaction}")
print(f"Buffer size: {len(conn._transaction_buffer)}")
print(f"Flushed: {conn._flushed}")
```

## Performance Issues

### Slow Queries (Remote)

Each query incurs HTTP latency. Optimize by:

1. **Using `select_related`/`prefetch_related`** to reduce query count:
   ```python
   # Bad: N+1 queries
   for author in Author.objects.all():
       print(author.books.all())
   
   # Good: 2 queries
   for author in Author.objects.prefetch_related('books').all():
       print(author.books.all())
   ```

2. **Using batch operations** for bulk inserts:
   ```python
   # Bad: N separate HTTP requests
   for item in items:
       MyModel.objects.create(name=item)
   
   # Good: 1 HTTP request
   MyModel.objects.bulk_create([MyModel(name=item) for item in items])
   ```

3. **Using `only()`/`defer()`** to fetch only needed columns:
   ```python
   MyModel.objects.only('name', 'email')
   ```

### Slow Migrations (Remote)

Each DDL statement is a separate HTTP request. For large migrations:

1. Run migrations during low-traffic periods
2. Consider using local mode for migration development

## Compatibility Issues

### Django Version Mismatch

This backend supports Django 4.2+. If you encounter issues with newer Django versions:

1. Check the [CHANGELOG](../CHANGELOG.md) for compatibility updates
2. File an issue on GitHub

### Third-Party Packages

Some Django packages assume PostgreSQL or MySQL-specific features. Common issues:

- **django-debug-toolbar**: May show "unsupported" for some features
- **django-rest-framework**: Should work, but test thoroughly
- **django-filter**: Should work with standard ORM operations

## Getting Help

1. Check this troubleshooting guide
2. Search [GitHub Issues](https://github.com/CyberCalculus/django-libsql-backend/issues)
3. Open a new issue with:
   - Django version
   - Python version
   - Backend version
   - Full error traceback
   - Minimal reproduction code
