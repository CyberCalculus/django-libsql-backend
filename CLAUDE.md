# CLAUDE.md — django-libsql-backend

## Overview

Django database backend for **libSQL / Turso** — dual-mode: remote Turso databases over HTTP REST API, or local SQLite files. Same ENGINE (`django_libsql`), same ORM, same migrations.

Connection type is auto-detected from the `NAME` setting in `DATABASES`.

### Quick Reference
- **Repo**: `git@github.com-second:CyberCalculus/django-libsql-backend.git`
- **PyPI**: https://pypi.org/project/django-libsql-backend/0.1.0/
- **Install**: `pip install django-libsql-backend`

## Build & Publish

```bash
# Build
rm -rf dist build *.egg-info
../.venv-build/bin/python -m build

# Upload to PyPI (token in ~/.pypirc, cybercalculus account)
../.venv-build/bin/twine upload dist/*

# Local dev install
pip install -e .
```

Build tools venv: `../.venv-build/` (contains `build`, `twine`)

## Package Structure

```
django_libsql/
├── __init__.py        # Exports DatabaseWrapper, __version__ (0.1.0)
├── base.py            # DatabaseWrapper, TursoCursor, TursoHTTPConnection, LocalSQLiteCursor
├── features.py        # SQLite-compatible DatabaseFeatures flags (39 flags)
├── operations.py      # SQL generation — date/time, upsert, operators, pattern ops
├── schema.py          # Proxy to Django's built-in SQLite schema editor
├── introspection.py   # Proxy to Django's built-in SQLite introspection
├── creation.py        # Test database create/destroy
└── client.py          # dbshell — delegates to `turso db shell`
```

## Architecture

### Dual-Mode Connection

`_is_local_name()` in `base.py` auto-detects the mode from the NAME string:

| NAME form | Mode | Example |
|---|---|---|
| HTTPS URL | Remote | `https://my-db.turso.io` |
| Bare hostname | Remote | `my-db.turso.io` |
| `libsql://` URL | Remote | `libsql://my-db.turso.io` (converted to https) |
| File path (absolute) | Local | `/var/data/db.sqlite3` |
| File path (relative) | Local | `./dev.db` |
| Bare filename | Local | `db.sqlite3` |
| File extensions | Local | `.sqlite3`, `.db`, `.sqlite`, `.s3db`, `.sl3` |
| `:memory:` | Local | In-memory SQLite |

### Remote Mode (TursoHTTPConnection)

- Each request = independent HTTP call to Turso REST API
- `/v1/execute` for single statements, `/v1/batch` for `executemany()`
- Stateless — no persistent connection, no transactions across requests, no PRAGMA persistence
- Transactions, savepoints, `atomic()` blocks are **no-ops** — each statement auto-commits
- Values serialized via `_py_value_to_turso_type()` → Turso typed-value JSON: `{type: "integer"|"real"|"text"|"blob"|"null", value: ...}`
- Values deserialized via `_turso_value_to_py()`
- Django `%s` placeholders converted to qmark `?` via `_convert_query()`
- Auth via Bearer token in Authorization header

### Local Mode (LocalSQLiteCursor)

- Uses Python stdlib `sqlite3` module
- `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` set on every new connection
- Full transaction support: `BEGIN`, `COMMIT`, `ROLLBACK`, savepoints
- `isolation_level` toggled: `None` (autocommit) / `DEFERRED` (implicit transactions)
- `check_same_thread=False` on connect for Django's multi-thread usage
- Also converts `%s` → `?` for consistency

### Django Integration Points

- `DatabaseWrapper` extends `BaseDatabaseWrapper`, sets `vendor = "libsql"`
- `SchemaEditorClass` → `DatabaseSchemaEditor` (proxies Django's built-in SQLite editor)
- `introspection_class` → `DatabaseIntrospection` (proxies Django's built-in)
- `client_class` → `DatabaseClient` (`dbshell` → `turso db shell`)
- `features_class` → `DatabaseFeatures` (39 SQLite-compatible feature flags)
- `ops_class` → `DatabaseOperations` (SQL generation for date/time/upsert/operators)
- 40 data type mappings, pattern ops, ESCAPE handling, FK constraint checks

## Key Design Decisions

- **Zero external dependencies** beyond Django — uses only stdlib `urllib`, `json`, `sqlite3`
- **Drop-in replacement** for Django's SQLite backend — same ENGINE pattern, OPTIONS dict, NAME semantics
- **Schema/introspection proxy** rather than reimplementing — inherits from Django's built-in classes and overrides only what differs
- **pyproject.toml**: `setuptools.build_meta` backend (not deprecated `_legacy`), SPDX `license = "MIT"`, `requires-python = ">=3.10"`, `Django>=4.2`

## Django Settings Usage

```python
# Remote (production)
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "https://my-db.turso.io",
        "AUTH_TOKEN": "jwt-token-here",
        "OPTIONS": {"timeout": 30},
    }
}

# Local (development)
DATABASES = {
    "default": {
        "ENGINE": "django_libsql",
        "NAME": "dev.sqlite3",
    }
}
```

## Environment

- **Python**: 3.13, PEP 668 protected (use venvs)
- **Django**: 6.0.5 (project), 4.2+ (package dependency)
- **Package metadata**: author=CyberCalculus, license=MIT, version=0.1.0
