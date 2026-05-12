# django-libsql-backend

Django database backend for **[libSQL](https://libsql.org/)** and **[Turso](https://turso.tech/)** — supports both remote Turso databases over HTTP and local SQLite files.

Drop-in replacement for Django's built-in SQLite backend. Use a local `.sqlite3` file during development, then switch to a production Turso URL with zero code changes — same ENGINE, same ORM, same migrations.

---

## Features

- **Dual-mode** — auto-detects local file paths vs remote URLs from the `NAME` setting
- **100% remote when you need it** — no local SQLite files, no file locks, no persistent connections
- **Full local when you need it** — real transactions, WAL mode, FK enforcement
- **SQLite-compatible** — delegates to Django's built-in SQLite schema editor and introspection
- **Migrations** — `makemigrations`, `migrate`, `sqlmigrate` work as expected
- **ORM** — full queryset API, aggregations, annotations, `ON CONFLICT` (upsert)
- **Admin** — Django admin works out of the box
- **Authentication** — password hashing, sessions, permissions
- **Type mapping** — Django model fields map to correct SQLite column types
- **Batch API** — `executemany()` uses Turso's `/v1/batch` endpoint for efficient bulk inserts

---

## Requirements

| Dependency | Minimum Version |
|---|---|
| Python | 3.10+ |
| Django | 4.2+ (tested on 5.0, 5.1, 6.0) |
| Turso / libSQL database | SQLite 3.31+ (Turso currently ships 3.45) |

The backend uses only Python's standard library (`urllib`, `json`) — **no additional Python packages are required**.

---

## Installation

```bash
pip install django-libsql-backend
```

---

## Quick Start

### 1. Get a Turso database

[Create a database](https://docs.turso.tech/sdk/ts/quickstart#step-1-create-a-database) and grab the HTTP URL and an auth token:

```bash
turso db create my-django-app
turso db tokens create my-django-app
```

### 2. Configure Django

In `settings.py`:

```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "https://my-django-app-org.turso.io",
        "AUTH_TOKEN": "your-jwt-auth-token-here",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}
```

`NAME` accepts any of these forms:

| Form | Example | Connection type |
|---|---|---|
| Full HTTPS URL | `https://my-db-org.turso.io` | Remote (Turso HTTP) |
| Bare hostname | `my-db-org.turso.io` | Remote (Turso HTTP) |
| `libsql://` URL | `libsql://my-db-org.turso.io` | Remote (converted to HTTPS) |
| Absolute file path | `/var/data/db.sqlite3` | Local (sqlite3) |
| Relative file path | `./local_dev.db` | Local (sqlite3) |
| Bare filename | `db.sqlite3` | Local (sqlite3) |
| In-memory | `:memory:` | Local (sqlite3)

### 3. Run migrations

```bash
python manage.py migrate
```

That's it. Django is now running against your remote Turso database.

### Local development workflow

During local development, just point `NAME` at a file path — no Turso account needed:

```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "dev.sqlite3",
    }
}
```

Run `python manage.py migrate` and you're developing locally with full SQLite transaction support, WAL mode, and FK enforcement. When you're ready to deploy, change `NAME` to your Turso URL and add the `AUTH_TOKEN` — that's the only change needed.

---

## Configuration Reference

| Setting | Required | Default | Description |
|---|---|---|---|
| `ENGINE` | Yes | — | `"django_libsql"` |
| `NAME` | Yes | — | Database URL, hostname, or local file path |
| `AUTH_TOKEN` | Remote only | `""` | Turso platform auth token (JWT) |
| `OPTIONS.timeout` | No | `30` | HTTP request timeout in seconds (remote only) |

### Environment variables

For production, store the auth token in an environment variable rather than hard-coding it:

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

---

## Architecture

```
┌──────────┐      HTTP POST /v1/execute       ┌──────────────┐
│  Django   │ ───────────────────────────────► │  Turso /     │
│  ORM /    │      HTTP POST /v1/batch         │  libSQL      │
│  Migrations│ ◄─────────────────────────────── │  Server      │
└──────────┘       JSON (typed-value)           └──────────────┘
```

### How it differs from Django's built-in SQLite backend

| | Django SQLite | django-libsql-backend (local) | django-libsql-backend (remote) |
|---|---|---|---|---|
| **Transport** | Local file I/O | Local file I/O | HTTP REST API |
| **Connection** | Persistent `sqlite3.Connection` | Persistent `sqlite3.Connection` | Stateless — each request = new connection |
| **Transactions** | Real SQLite transactions | Real SQLite transactions | Each statement auto-commits independently |
| **Savepoints** | Supported | Supported | Not supported |
| **PRAGMAs** | Persistent per connection | Persistent per connection | Reset every HTTP request |
| **File locking** | Yes | Yes | None — Turso handles concurrency |
| **Schema changes** | `_remake_table` | `_remake_table` | Same, each DDL is its own HTTP call |

### Internal module layout

```
django_libsql/
├── __init__.py       # Exports DatabaseWrapper, __version__
├── base.py            # DatabaseWrapper, TursoCursor, TursoHTTPConnection
├── features.py        # SQLite-compatible feature flags (39 flags)
├── operations.py      # SQL generation (date/time, upsert, operators)
├── schema.py          # Proxy to Django's SQLite schema editor
├── introspection.py   # Proxy to Django's SQLite introspection
├── creation.py        # Test database create/destroy
└── client.py          # CLI: python manage.py dbshell → turso db shell
```

---

## Supported Django Field Types

All standard Django model fields are supported with correct SQLite column types:

| Django Field | SQLite Column Type |
|---|---|
| `AutoField` / `BigAutoField` / `SmallAutoField` | `integer AUTOINCREMENT` |
| `CharField` / `SlugField` / `FileField` / `FilePathField` | `varchar(N)` |
| `TextField` | `text` |
| `IntegerField` | `integer` |
| `BigIntegerField` | `bigint` |
| `BooleanField` | `bool` |
| `FloatField` | `real` |
| `DecimalField` | `decimal` |
| `DateField` | `date` |
| `DateTimeField` | `datetime` |
| `TimeField` | `time` |
| `DurationField` | `bigint` |
| `BinaryField` | `BLOB` |
| `JSONField` | `text` (with `JSON_VALID` check constraint) |
| `UUIDField` | `char(32)` |
| `IPAddressField` | `char(15)` |
| `GenericIPAddressField` | `char(39)` |
| `PositiveIntegerField` | `integer unsigned` |

---

## Known Limitations

These apply only to **remote** (Turso HTTP) mode. Local mode has full SQLite transaction support.

### Stateless HTTP connection

Each Turso HTTP request creates a new SQLite connection. This means:

- **`PRAGMA` settings do not persist** between requests. `PRAGMA foreign_keys = OFF` in request A does not affect request B.
- **`SET` / transaction state** is not shared across calls.

### Transactions

`commit()`, `rollback()`, `savepoints`, and `atomic()` blocks are **no-ops** — each SQL statement is its own auto-committed transaction over HTTP. For most web applications this is acceptable because Django's default autocommit mode already wraps each request in a single implicit transaction.

If you need multi-statement atomicity, consider:
- Using Turso's batch API (the backend uses it automatically for `executemany()`)
- Calling a server-side function that groups statements

### Foreign key constraints

Since FK pragmas don't persist, the schema editor bypasses Django's FK-disabling requirement. This works for **creating new tables** (the common path during initial `migrate`). If you later use `ALTER TABLE` operations that rebuild tables, Turso handles the DDL isolation server-side.

---

## Development & Testing

```bash
# Clone the repo
git clone https://github.com/sinescode/django-libsql-backend.git
cd django-libsql-backend

# Install in development mode
pip install -e .

# Run Django checks
python manage.py check

# Run migrations
python manage.py migrate
```

---

## Troubleshooting

### "Turso HTTP 401"
Your auth token is invalid or expired. Generate a new one:
```bash
turso db tokens create <db-name>
```

### "Turso connection error: timed out"
Check network connectivity to your Turso database URL. Increase the timeout in `OPTIONS` if needed:
```python
"OPTIONS": {"timeout": 60}
```

### "RuntimeError: No connection established"
The HTTP connection hasn't been initialized. This usually means the database settings are misconfigured — verify `NAME` and `AUTH_TOKEN`.

### Migrations fail with "no such table: django_migrations"
Run `python manage.py migrate` — this table is created by Django's first migration.

---

## License

MIT. See [LICENSE](LICENSE) for details.

---

## Related Projects

- [Turso](https://turso.tech/) — Managed libSQL platform
- [libSQL](https://libsql.org/) — Open-source SQLite fork
- [Django SQLite backend](https://docs.djangoproject.com/en/stable/ref/databases/#sqlite-notes) — Built-in backend this project is modeled on

---

## Contributing

Issues and pull requests are welcome. Before opening a PR, please:

1. Run `python manage.py check` against a Turso database
2. Verify `python manage.py migrate` runs cleanly
3. Test basic ORM operations (create, read, update, delete)
