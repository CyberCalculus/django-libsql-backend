# CLAUDE.md — django-libsql-backend

## Overview

Django database backend for **libSQL / Turso** — dual-mode: remote Turso databases over HTTP REST API, or local SQLite files. Same ENGINE (`django_libsql`), same ORM, same migrations.

Connection type is auto-detected from the `NAME` setting in `DATABASES`.

## Build & Publish

```bash
# Build
rm -rf dist build *.egg-info
python -m build

# Upload to PyPI
twine upload dist/*

# Local dev install
pip install -e .
```

## Package structure

```
django_libsql/
├── __init__.py       # Exports DatabaseWrapper, __version__
├── base.py            # DatabaseWrapper, TursoCursor, TursoHTTPConnection, LocalSQLiteCursor
├── features.py        # SQLite-compatible DatabaseFeatures flags
├── operations.py      # SQL generation (date/time, upsert, operators)
├── schema.py          # Proxy to Django's built-in SQLite schema editor
├── introspection.py   # Proxy to Django's built-in SQLite introspection
├── creation.py        # Test database create/destroy
└── client.py          # dbshell → turso db shell
```

## Architecture

- **Remote mode** (`TursoHTTPConnection`): Each request is an independent HTTP call to Turso's `/v1/execute` or `/v1/batch` endpoints. Stateless — no persistent connection, no transactions across requests, no PRAGMA persistence.
- **Local mode** (stdlib `sqlite3`): Full SQLite with WAL mode, FK enforcement, real transactions and savepoints.
- `_is_local_name(name)` in `base.py` decides which mode to use based on the `NAME` string.
- Format-style `%s` placeholders are converted to qmark `?` for both modes.
- Values are serialized to Turso's typed-value JSON format (`_py_value_to_turso_type`) and deserialized back (`_turso_value_to_py`).
- Schema editor and introspection delegate to Django's built-in SQLite classes.
- `executemany()` uses the `/v1/batch` endpoint for bulk operations.

## Key design decisions

- **No external dependencies** beyond Django — uses only stdlib `urllib`, `json`, `sqlite3`.
- **Drop-in replacement** for Django's SQLite backend — same ENGINE name pattern, same settings structure.
- **Schema/introspection proxy** rather than reimplementing — inherits from Django's `DatabaseSchemaEditor` and `DatabaseIntrospection` where possible.

## PyPI

Package: `django-libsql-backend` — https://pypi.org/project/django-libsql-backend/
Token in `~/.pypirc`
GitHub: `git@github.com:CyberCalculus/django-libsql-backend.git`
