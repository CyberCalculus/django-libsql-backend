# AGENTS.md — django-libsql-backend

## What this is

Django database backend (`django_libsql`) for libSQL/Turso. Triple-mode: HTTP REST API, WebSocket (Hrana), or local SQLite files. Single package, no tests in this repo.

## Build & publish

```bash
rm -rf dist build *.egg-info
python -m build
twine upload dist/*  # token in ~/.pypirc
```

CI publishes to PyPI on `v*` tag push via trusted publishing (`.github/workflows/publish.yml`).

## Dev install

```bash
pip install -e .
```

## Key constraints

- **Zero runtime deps** beyond Django (`>=4.2`). Only stdlib `urllib`, `json`.
- **Python 3.10+** (`requires-python = ">=3.10"`).
- Package name on PyPI: `django-libsql-backend`. Import name: `django_libsql`.
- The `NAME` setting auto-detects mode: HTTPS URL or bare hostname or `libsql://` = remote HTTP; `ws://`/`wss://` = remote Hrana (WebSocket); file path = local SQLite.

## Architecture

All backend logic is in `django_libsql/`:

| File | Role |
|---|---|
| `base.py` | `DatabaseWrapper`, `TursoCursor`, `TursoHTTPConnection`, `HranaCursor` |
| `operations.py` | SQL generation (date/time, upsert, operators) |
| `features.py` | SQLite-compatible `DatabaseFeatures` flags |
| `schema.py` | Proxies Django's built-in SQLite schema editor |
| `introspection.py` | Proxies Django's built-in SQLite introspection |
| `creation.py` | Test database create/destroy |
| `client.py` | `dbshell` command — delegates to `turso db shell` or `sqlite3` |
| `functions.py` | Custom DB functions (delegates to Django's `_functions` module) |

## Documentation

Full documentation is in `docs/`:

- `docs/index.md` — overview and quick start
- `docs/configuration.md` — all settings and options
- `docs/architecture.md` — internals, transaction model, type conversion
- `docs/limitations.md` — remote mode constraints, unavailable functions
- `docs/troubleshooting.md` — common errors and fixes
- `docs/contributing.md` — development setup and guidelines

## Things to know

- **No test suite in this repo.** Integration testing happens in a companion Django project at `../dbpytursomodule/` (parent directory).
- **Linter/formatter**: `ruff` config in `pyproject.toml`. Run `ruff check` and `ruff format` before committing.
- Remote HTTP mode is stateless — no persistent connection, no real transactions. `atomic()` blocks are no-ops (writes buffered client-side, flushed on commit).
- Remote Hrana (WebSocket) mode uses `libsql-client` for persistent connections with real transactions.
- `libsql://` URLs are auto-converted to HTTPS for the REST API. Use `ws://`/`wss://` for WebSocket.
- Values are serialized to Turso's typed-value JSON format (`{type: "integer"|"real"|"text"|"blob"|"null", value: ...}`).
- Django `%s` placeholders are converted to qmark `?` before sending.
- `setuptools.build_meta` backend (not the deprecated `_legacy`).
- PyPI account: `cybercalculus`. Token in `~/.pypirc`.
- GitHub remote: `git@github.com-second:CyberCalculus/django-libsql-backend.git` (uses SSH host alias for secondary GitHub account).
