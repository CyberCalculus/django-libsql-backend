# Configuration Reference

## Database Settings

```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "https://my-db.turso.io",
        "AUTH_TOKEN": "your-jwt-token",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}
```

## Settings

### ENGINE (required)

```python
"ENGINE": "django_libsql"
```

The Django database engine identifier.

### NAME (required)

The database connection string. The backend auto-detects the connection mode from this value:

| Format | Example | Mode |
|---|---|---|
| Full HTTPS URL | `https://my-db-org.turso.io` | Remote (HTTP) |
| Bare hostname | `my-db-org.turso.io` | Remote (HTTP) |
| `libsql://` URL | `libsql://my-db-org.turso.io` | Remote (HTTP, converted to HTTPS) |
| `ws://`/`wss://` URL | `wss://my-db-org.turso.io` | Remote (Hrana/WebSocket) |
| Absolute file path | `/var/data/db.sqlite3` | Local (sqlite3) |
| Relative file path | `./dev.db` | Local (sqlite3) |
| Bare filename | `db.sqlite3` | Local (sqlite3) |
| In-memory | `:memory:` | Local (sqlite3) |

> **Note:** `libsql://` URLs are automatically converted to HTTPS for the Turso REST API. Use `ws://` or `wss://` explicitly for WebSocket/Hrana mode (requires `pip install django-libsql-backend[hrana]`).

**Detection logic:**
1. Starts with `http://`, `https://` → Remote (HTTP)
2. Starts with `libsql://` → Remote (HTTP, converted to HTTPS)
3. Starts with `ws://`, `wss://` → Remote (Hrana/WebSocket, requires `libsql-client`)
4. Starts with `/` or `.` → Local
5. Ends with `.sqlite3`, `.db`, `.sqlite`, `.s3db`, `.sl3` → Local
6. Contains `.` but no `/` or `\` → Remote (bare hostname)
7. Everything else → Local

### AUTH_TOKEN (remote only)

```python
"AUTH_TOKEN": "eyJhbGciOi..."
```

Turso platform authentication token (JWT). Required for remote connections. Generate with:

```bash
turso db tokens create <db-name>
```

### OPTIONS (optional)

```python
"OPTIONS": {
    "timeout": 30,
    "transaction_mode": "DEFERRED",
    "init_command": "PRAGMA journal_mode=WAL",
}
```

#### timeout

- **Type**: `int`
- **Default**: `30`
- **Applies to**: Remote mode only
- HTTP request timeout in seconds.

#### transaction_mode

- **Type**: `str | None`
- **Default**: `None`
- **Applies to**: Local mode only
- SQLite transaction mode for `BEGIN` statements. Must be one of:
  - `"DEFERRED"` — acquires a shared lock (default SQLite behavior)
  - `"IMMEDIATE"` — acquires a reserved lock immediately
  - `"EXCLUSIVE"` — acquires an exclusive lock immediately
  - `None` — uses `BEGIN` without a mode (equivalent to `DEFERRED`)

Invalid values raise `ImproperlyConfigured`.

#### init_command

- **Type**: `str`
- **Default**: `""`
- **Applies to**: Local mode only
- Semicolon-separated SQL commands to execute after connecting. Example:

```python
"init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL"
```

## Environment Variables

For production, store sensitive values in environment variables:

```python
import os

DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": os.environ["TURSO_DB_URL"],
        "AUTH_TOKEN": os.environ["TURSO_AUTH_TOKEN"],
    }
}
```

## Test Database Configuration

```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "dev.sqlite3",
        "TEST": {
            "NAME": "test_dev.sqlite3",
        },
    }
}
```

### TEST.NAME

- **Local mode**: Defaults to `{NAME}_test{ext}` (e.g., `dev_test.sqlite3`)
- **Remote mode**: Defaults to the production `NAME` (Turso databases cannot be created on the fly)
- **In-memory**: Uses `file:memorydb_{alias}?mode=memory&cache=shared`

### TEST.MIRROR

Not supported. Use `TEST.NAME` to point at a separate database instead.

## Multiple Databases

```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "dev.sqlite3",
    },
    "production": {
        "ENGINE": "django_libsql",
        "NAME": "https://prod-db.turso.io",
        "AUTH_TOKEN": os.environ["TURSO_PROD_TOKEN"],
    },
}
```

## Django Settings Compatibility

This backend supports all standard Django database settings:

| Setting | Supported | Notes |
|---|---|---|
| `ENGINE` | Yes | `"django_libsql"` |
| `NAME` | Yes | File path or URL |
| `USER` | No | Ignored (Turso uses `AUTH_TOKEN`) |
| `PASSWORD` | No | Ignored (Turso uses `AUTH_TOKEN`) |
| `HOST` | No | Ignored (use `NAME` for the full URL) |
| `PORT` | No | Ignored (embedded in `NAME`) |
| `OPTIONS` | Yes | See above |
| `TEST` | Yes | See above |
| `AUTOCOMMIT` | Yes | Default Django behavior |
| `CONN_MAX_AGE` | No | Remote mode is stateless |
| `CONN_HEALTH_CHECKS` | No | Not implemented |
