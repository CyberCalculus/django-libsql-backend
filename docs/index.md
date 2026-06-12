# django-libsql-backend

A drop-in Django database backend for **[libSQL](https://libsql.org/)** and **[Turso](https://turso.tech/)**.

## What is this?

`django-libsql-backend` lets you use Turso (managed libSQL) or local SQLite files as your Django database with zero code changes between development and production.

### Key Features

- **Triple-mode**: auto-detects local files, remote HTTP, or WebSocket (Hrana) from the `NAME` setting
- **Zero dependencies**: uses only Python's stdlib (`urllib`, `json`)
- **Full ORM support**: querysets, aggregations, annotations, upserts
- **Migrations**: `makemigrations`, `migrate`, `sqlmigrate` work out of the box
- **Admin & Auth**: Django admin, password hashing, sessions, permissions
- **Batch API**: efficient bulk operations via Turso's `/v1/batch` endpoint

## Quick Start

### Installation

```bash
pip install django-libsql-backend
```

For WebSocket/Hrana mode:
```bash
pip install django-libsql-backend[hrana]
```

### Configuration

```python
import os

DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": os.environ.get("TURSO_DB_URL", "dev.sqlite3"),
        "AUTH_TOKEN": os.environ.get("TURSO_AUTH_TOKEN", ""),
    }
}
```

### Run migrations

```bash
python manage.py migrate
```

## How it works

The backend operates in three modes:

### Local mode (file path)

When `NAME` is a file path (e.g., `dev.sqlite3`, `/var/data/db.sqlite3`), the backend uses Python's built-in `sqlite3` module with:
- WAL journal mode
- Foreign key enforcement
- Full transaction and savepoint support
- All Django custom SQL functions

### Remote mode (HTTP URL)

When `NAME` is a URL (e.g., `https://my-db.turso.io` or `libsql://my-db.turso.io`), the backend communicates with Turso via HTTP REST API:
- Stateless HTTP requests (each request = new SQLite connection)
- Client-side write buffering for atomicity
- Automatic placeholder conversion (`%s` → `?`)
- Turso typed-value JSON serialization

### Hrana mode (WebSocket)

When `NAME` is a WebSocket URL (e.g., `wss://my-db.turso.io`), the backend uses the `libsql-client` package for persistent connections:
- Persistent server-side state
- Real transactions and savepoints
- PRAGMA persistence across queries

## Architecture

```
┌──────────┐      HTTP POST /v1/execute       ┌──────────────┐
│  Django   │ ───────────────────────────────► │  Turso /     │
│  ORM /    │      HTTP POST /v1/batch         │  libSQL      │
│  Migrations│ ◄─────────────────────────────── │  Server      │
└──────────┘       JSON (typed-value)           └──────────────┘
```

### Module Layout

```
django_libsql/
├── __init__.py       # Exports DatabaseWrapper, __version__
├── base.py           # DatabaseWrapper, TursoCursor, TursoHTTPConnection, HranaCursor
├── features.py       # SQLite-compatible feature flags
├── operations.py     # SQL generation (date/time, upsert, operators)
├── schema.py         # Proxy to Django's SQLite schema editor
├── introspection.py  # Proxy to Django's SQLite introspection
├── creation.py       # Test database create/destroy
├── client.py         # CLI: dbshell (turso or sqlite3)
└── functions.py      # Custom DB functions (local mode)
```

## Further Reading

- [Configuration Reference](configuration.md) — all settings and options
- [Architecture Deep Dive](architecture.md) — internals, transaction model, type conversion
- [Known Limitations](limitations.md) — remote mode constraints, unavailable functions
- [Troubleshooting](troubleshooting.md) — common errors and fixes
- [Contributing](contributing.md) — development setup and guidelines
- [Changelog](../CHANGELOG.md) — version history and release notes
