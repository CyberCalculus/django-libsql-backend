# Changelog

All notable changes to `django-libsql-backend` are documented in this file.

---

## [0.1.0] — 2026-05-12

### Added

- Initial release
- `DatabaseWrapper` with Turso HTTP REST API transport (`/v1/execute`, `/v1/batch`)
- `TursoCursor` — DB-API 2.0 compatible cursor with Django `%s` → qmark `?` conversion
- `TursoHTTPConnection` — minimal HTTP client with Bearer auth
- Complete data type mapping for all Django model fields
- SQL generation (`DatabaseOperations`) — date/time functions, upsert, operators
- Feature flags (`DatabaseFeatures`) — 39 SQLite-compatible flags
- Schema editor proxy — delegates to Django's SQLite schema editor, bypasses FK pragma checks for stateless HTTP
- Introspection proxy — delegates to Django's SQLite introspection
- `DatabaseClient` — `python manage.py dbshell` integration with `turso db shell`
- `DatabaseCreation` — test database create/destroy
- Support for Django 4.2, 5.0, 5.1, 6.0
- Support for Python 3.10, 3.11, 3.12, 3.13
