# Changelog

All notable changes to `django-libsql-backend` are documented in this file.

---

## [0.1.2] — 2026-06-12

### Security

- SSRF prevention: bare hostname validation now rejects path traversal (`..`) in NAME setting (audit 1.6)
- Error messages truncate response body to 500 chars to avoid leaking sensitive data (audit 1.7)

### Fixed

- `libsql://` URLs now correctly route to HTTP REST API (converted to `https://`) instead of WebSocket
- `last_executed_query` uses regex replacement instead of `%` formatting to avoid conflicts with SQL `%` characters
- `_set_autocommit` uses `""` instead of `"DEFERRED"` for correct savepoint behavior
- `_start_transaction_under_autocommit` routes through `self.cursor()` for thread safety
- `_savepoint_rollback` warns when rolling back after auto-flush
- `is_usable()` returns `True` for local mode (matches Django SQLite)
- `init_connection_state` delegates to base class (removed redundant override)
- `transaction_mode` renamed from `_transaction_mode` for API compatibility
- `atomic_transactions` set to `False` for both modes (matches Django SQLite)
- `create_test_db` now runs migrations and manages connection settings
- `destroy_test_db` accepts `suffix` parameter and restores settings
- `is_in_memory_db` handles `Path` objects and checks `TEST["NAME"]`
- `_destroy_test_db` drops indexes, views, and triggers (not just tables)
- `schema.py` `__enter__` returns proxy object in HTTP mode
- `adapt_datetimefield_value` and `adapt_timefield_value` match Django behavior
- `format_json_path_numeric_index` matches Django implementation
- `time_extract_sql` includes `"second"` fallback
- `client.py` supports local mode (uses `sqlite3` CLI)
- `functions.py` adds defensive import with clear error message
- `django_test_expected_failures` and `django_test_skips` expanded
- `get_database_version()` logs a warning when falling back to `(3, 0, 0)`

### Changed

- `features.py` `_is_local` now delegates to `_get_connection_mode()` from `base.py` (DRY)
- `operations.py` extract/trunc methods refactored with shared `_common_extract_sql()`, `_quarter_trunc_sql()`, `_week_trunc_sql()` helpers
- `operations.py` `_UNSUPPORTED_TEXT_FUNCTIONS` moved to module level
- `base.py` savepoint methods use early-return guard clauses
- `base.py` `_py_value_to_turso_type()` handles `str` explicitly (no spurious warnings)
- `creation.py` flattened nested if-return chain in `_destroy_test_db()`

### Added

- `_clone_test_db` for parallel test execution support
- `pyproject.toml` Documentation and Changelog URLs
- `pyproject.toml` `[tool.ruff]` linting config
- `.github/workflows/ci.yml` — lint + test matrix (Python 3.10-3.13, Django 4.2-6.0)
- Comprehensive documentation in `docs/` directory
- Configuration reference, architecture deep dive, limitations, troubleshooting, contributing guides
- `from __future__ import annotations` in all modules for modern type hint support

---

## [0.1.0] — 2026-05-12

### Added

- Initial release
- `DatabaseWrapper` with Turso HTTP REST API transport (`/v1/execute`, `/v1/batch`)
- `TursoCursor` — DB-API 2.0 compatible cursor with Django `%s` → qmark `?` conversion
- `TursoHTTPConnection` — minimal HTTP client with Bearer auth
- Complete data type mapping for all Django model fields
- SQL generation (`DatabaseOperations`) — date/time functions, upsert, operators
- Feature flags (`DatabaseFeatures`) — SQLite-compatible flags
- Schema editor proxy — delegates to Django's SQLite schema editor, bypasses FK pragma checks for stateless HTTP
- Introspection proxy — delegates to Django's SQLite introspection
- `DatabaseClient` — `python manage.py dbshell` integration with `turso db shell`
- `DatabaseCreation` — test database create/destroy
- Local SQLite support — auto-detects file paths vs remote URLs from `NAME`
- `LocalSQLiteCursor` — wraps `sqlite3.Cursor` with `%s` → `?` conversion
- Full transaction support for local connections (WAL mode, savepoints, FK enforcement)
- Support for Django 4.2, 5.0, 5.1, 6.0
- Support for Python 3.10, 3.11, 3.12, 3.13
