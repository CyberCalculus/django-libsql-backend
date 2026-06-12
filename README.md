# django-libsql-backend

[![PyPI version](https://img.shields.io/pypi/v/django-libsql-backend.svg)](https://pypi.org/project/django-libsql-backend/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-libsql-backend.svg)](https://pypi.org/project/django-libsql-backend/)
[![Django versions](https://img.shields.io/pypi/dependents/django-libsql-backend.svg?label=django)](https://pypi.org/project/django-libsql-backend/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A drop-in Django database backend for **[libSQL](https://libsql.org/)** and **[Turso](https://turso.tech/)**. Supports remote Turso databases over HTTP/WebSocket and local SQLite files.

Use a local `.sqlite3` file during development, then switch to a production Turso URL with zero code changes — same `ENGINE`, same ORM, same migrations.

---

## Features

- **Triple-mode** — auto-detects local file paths, remote HTTP URLs, or WebSocket (Hrana) from the `NAME` setting
- **Zero external dependencies** — uses only Python's stdlib (`urllib`, `json`)
- **Full ORM support** — querysets, aggregations, annotations, `ON CONFLICT` (upsert)
- **Migrations** — `makemigrations`, `migrate`, `sqlmigrate` work out of the box
- **Admin & Auth** — Django admin, password hashing, sessions, permissions
- **Batch API** — `executemany()` uses Turso's `/v1/batch` endpoint for efficient bulk operations
- **SQLite-compatible** — delegates to Django's built-in SQLite schema editor and introspection
- **Django 4.2+** — tested on 5.0, 5.1, and 6.0

---

## Requirements

| Dependency | Minimum |
|---|---|
| Python | 3.10+ |
| Django | 4.2+ |
| Turso / libSQL | SQLite 3.31+ |

---

## Installation

```bash
pip install django-libsql-backend
```

For WebSocket/Hrana mode:
```bash
pip install django-libsql-backend[hrana]
```

---

## Quick Start

### 1. Get a Turso database

```bash
turso db create my-django-app
turso db tokens create my-django-app
```

### 2. Configure Django

```python
import os

DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": os.environ["TURSO_DB_URL"],
        "AUTH_TOKEN": os.environ["TURSO_AUTH_TOKEN"],
        "OPTIONS": {
            "timeout": 30,
        },
    }
}
```

### 3. Run migrations

```bash
python manage.py migrate
```

### Local development

Point `NAME` at a file path — no Turso account needed:

```python
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "dev.sqlite3",
    }
}
```

When ready to deploy, change `NAME` to your Turso URL and add `AUTH_TOKEN`. Zero code changes required.

---

## Configuration Reference

| Setting | Required | Default | Description |
|---|---|---|---|
| `ENGINE` | Yes | — | `"django_libsql"` |
| `NAME` | Yes | — | Database URL, hostname, or local file path |
| `AUTH_TOKEN` | Remote only | `""` | Turso platform auth token (JWT) |
| `OPTIONS.timeout` | No | `30` | HTTP request timeout in seconds |
| `OPTIONS.transaction_mode` | No | `None` | SQLite transaction mode: `"DEFERRED"`, `"EXCLUSIVE"`, or `"IMMEDIATE"` (local only) |
| `OPTIONS.init_command` | No | `""` | SQL commands to run on connection (local only, semicolon-separated) |

### NAME value formats

| Format | Example | Mode |
|---|---|---|
| Full HTTPS URL | `https://my-db-org.turso.io` | Remote (HTTP) |
| Bare hostname | `my-db-org.turso.io` | Remote (HTTP) |
| `libsql://` URL | `libsql://my-db-org.turso.io` | Remote (HTTP, converted to HTTPS) |
| `ws://`/`wss://` URL | `wss://my-db-org.turso.io` | Remote (Hrana/WebSocket) |
| Absolute path | `/var/data/db.sqlite3` | Local |
| Relative path | `./dev.db` | Local |
| In-memory | `:memory:` | Local |

> **Note:** `libsql://` URLs are automatically converted to HTTPS for the Turso REST API. Use `ws://` or `wss://` explicitly for WebSocket/Hrana mode (requires `pip install django-libsql-backend[hrana]`).

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

| | Django SQLite | django-libsql (local) | django-libsql (remote) |
|---|---|---|---|
| **Transport** | File I/O | File I/O | HTTP REST API |
| **Connection** | Persistent | Persistent | Stateless — each request = new connection |
| **Transactions** | Real | Real | Buffered writes flushed as batch on commit |
| **Savepoints** | Yes | Yes | Client-side buffer snapshots only |
| **DDL rollback** | Yes | Yes | Not supported — each DDL auto-commits |
| **PRAGMAs** | Persistent | Persistent | Reset every HTTP request |

### Internal module layout

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

---

## Known Limitations

These apply only to **remote** (Turso HTTP) mode. Local mode has full SQLite support.

### Stateless HTTP connection

Each Turso HTTP request creates a new SQLite connection. PRAGMA settings do not persist between requests. DDL cannot be rolled back.

### Transactions

Remote mode uses **client-side write buffering** for best-effort atomicity:

- Writes inside `atomic()` blocks are buffered in memory
- On commit, all buffered writes are flushed as a single batch
- Reading inside a transaction auto-flushes buffered writes first (read-your-writes)
- Accessing `cursor.lastrowid` also triggers a flush
- Once flushed, writes cannot be rolled back

### Unavailable SQL functions

These Django ORM functions use Python-registered SQL functions not available on remote servers:

- **Hash**: `MD5`, `SHA1`, `SHA224`, `SHA256`, `SHA384`, `SHA512`
- **String**: `LPad`, `RPad`, `Repeat`, `Reverse`
- **Math**: `Cot`, `Sign` (rewritten automatically), `BitXor` (raises error)
- **Aggregate**: `StdDev`, `Variance`

### Timezone handling

SQLite has no native timezone support. Results are correct when the database stores UTC (Django's default).

---

## Troubleshooting

### "Turso HTTP 401"
Your auth token is invalid or expired. Generate a new one:
```bash
turso db tokens create <db-name>
```

### "Turso connection error: timed out"
Increase the timeout:
```python
"OPTIONS": {"timeout": 60}
```

### "RuntimeError: No connection established"
Verify `NAME` and `AUTH_TOKEN` in your database settings.

---

## Development

```bash
git clone https://github.com/CyberCalculus/django-libsql-backend.git
cd django-libsql-backend
pip install -e .
python manage.py check
python manage.py migrate
```

---

## License

MIT. See [LICENSE](LICENSE).

---

## Related

- [Turso](https://turso.tech/) — Managed libSQL platform
- [libSQL](https://libsql.org/) — Open-source SQLite fork
- [Django SQLite backend](https://docs.djangoproject.com/en/stable/ref/databases/#sqlite-notes)
