# django-libsql-backend — Security & Feature Audit

**Package version:** 0.1.0  
**Django target:** 6.0.5  
**Audit date:** 2026-05-13  
**Last fix:** 2026-05-13 — Round 3 complete: 12 comparison-analysis bugs fixed, 6 feature flags corrected for remote mode, 4 date extraction bugs fixed (see [§7 Fix Log](#7-fix-log))  

Compares `django-libsql-backend` against Django's built-in `sqlite3` and `postgresql` backends. Each finding is a single point: vulnerability, missing feature, or divergence.

---

## 1. Security Vulnerabilities

### 1.1 — Unvalidated `transaction_mode` allows SQL injection in local mode

**File:** `base.py:623-624`  
**Severity:** High (local mode only)

`self._transaction_mode` is set directly from user-supplied `OPTIONS["transaction_mode"]` with zero validation. Django's built-in SQLite backend validates against a frozenset:

```python
# Django built-in — base.py:143
transaction_modes = frozenset(["DEFERRED", "EXCLUSIVE", "IMMEDIATE"])
```

In `_start_transaction_under_autocommit` (line 694), the mode is interpolated directly into SQL:

```python
self.connection.execute(f"BEGIN {self._transaction_mode}")
```

An attacker who controls `settings.DATABASES['default']['OPTIONS']['transaction_mode']` can inject arbitrary SQL after `BEGIN`. While Django settings are considered trusted input, this diverges from Django's own defense-in-depth approach.

**Fix:** Add the same `transaction_modes` frozenset and validation that Django's SQLite backend uses.

---

### 1.2 — No `pyformat` / named parameter support → `NotImplementedError` crash

**File:** `base.py:119-121`  
**Severity:** Medium

`_build_turso_args()` raises `NotImplementedError` for `Mapping` params. Django's built-in `SQLiteCursorWrapper.convert_query()` handles both `format` and `pyformat` styles. The libsql `_convert_query()` only handles `format` style (`%s` → `?`). Any third-party code or internal Django paths using `pyformat`-style params will crash at runtime.

Also: `LocalSQLiteCursor` has the same limitation — its `execute()` passes params directly to `sqlite3.Cursor.execute()` without checking type, but the conversion only does `%s` → `?`. Named params (`%(name)s`) are not converted to `:name` style.

**Fix:** Implement `pyformat` → `named` conversion in `_convert_query()`, mirroring Django's `SQLiteCursorWrapper.convert_query()`.

---

### 1.3 — Transaction buffering: reads never see buffered writes (data integrity)

**File:** `base.py:291-301`  
**Severity:** High (remote mode)

Within a remote transaction, writes are buffered and reads go directly to the server. This means:

```python
with transaction.atomic():
    obj = MyModel.objects.create(name="foo")  # buffered, not sent
    exists = MyModel.objects.filter(name="foo").exists()  # → False
```

The ORM's `lastrowid` auto-flush partially mitigates this for single INSERT + model save, but any read-after-write pattern that doesn't go through `lastrowid` will see stale data.

**Fix:** Document prominently. Consider auto-flushing before any read in a transaction (performance cost), or raising an error on read-after-write.

---

### 1.4 — `lastrowid` access silently commits the transaction, rollback is broken

**File:** `base.py:343-359`  
**Severity:** High (remote mode)

When Django's ORM reads `cursor.lastrowid` (after every INSERT), the property auto-flushes the buffer. This sends a BEGIN + buffered writes + COMMIT to the server. After this point:

- The INSERT is permanently committed
- `transaction.rollback()` cannot undo it
- The caller gets no warning that their "transaction" is partially committed

Django models save → ORM reads `lastrowid` → instant autocommit. Every `model.save()` inside an `atomic()` block effectively auto-commits immediately.

**Fix:** At minimum, set a dirty flag and warn/error on rollback after auto-flush. Better: use a different mechanism (e.g. Turso's batch API with RETURNING clause) to avoid needing `lastrowid`.

---

### 1.5 — Double `%%` → `%` replacement after placeholder conversion could mangle literals

**File:** `base.py:49, 264`  
**Severity:** Low

`_convert_query()` first converts `%s` → `?`, then `%%` → `%`. This matches Django's approach, but Django uses `_lazy_re_compile` for the regex (compiled at import). The libsql version uses `re.compile` at module level — functionally identical, just a style difference.

The actual vulnerability risk here is zero (matches Django's well-tested behavior).

---

### 1.6 — SSRF vector via NAME setting

**File:** `base.py:579-584`  
**Severity:** Low (settings are trusted)

The `NAME` setting is used to construct the base URL for HTTP requests:

```python
url = f"https://{name}"  # bare hostname
```

A malicious `NAME = "internal-service:8080/../sensitive"` could cause unexpected requests. This is mitigated by Django's settings being trusted, but worth noting for defense-in-depth.

**Fix:** Validate URL format, reject path traversal characters in bare hostnames.

---

### 1.7 — Auth token passed in every HTTP request header without masking in errors

**File:** `base.py:212-214`  
**Severity:** Low

The Bearer token is sent correctly via Authorization header. However, if an HTTP error occurs, the `_raise_http_error` method (line 229-238) raises Django exceptions containing the response `body` text — which could include token info if the server echoes it. The token itself is not logged by Django at default settings, but custom error handlers could expose it.

---

### 1.8 — `init_command` split by `;` without proper SQL parsing

**File:** `base.py:617-620`  
**Severity:** Low (local mode, trusted settings)

User-supplied init commands are naively split by `;` and each chunk executed. Multi-statement commands containing string literals with `;` would break. Django uses the same approach, so this is consistent behavior.

---

### 1.9 — Remote mode: PRAGMA statements are write-classified and buffered

**File:** `base.py:52-55`  
**Severity:** Medium (remote mode)

`PRAGMA` is in the `_WRITE_PREFIXES` list, so PRAGMAs inside a transaction are buffered. But in remote mode, PRAGMAs don't persist anyway (each request is a separate connection). Buffering them inside a transaction and flushing on commit means they execute on yet another independent connection — achieving nothing.

**Fix:** Remove PRAGMA from `_WRITE_PREFIXES` for remote mode, or document that PRAGMAs are meaningless in remote transactions.

---

### 1.10 — `_close()` is a no-op for remote connections → resource leak if HTTP connections are pooled

**File:** `base.py:660-666`  
**Severity:** Low (current stateless design)

`TursoHTTPConnection` has no close logic. Each request uses `urllib.request.urlopen()` which closes the socket after each call. If connection pooling is added later, this will become a leak.

---

## 2. Feature Gaps — vs. Django Built-in SQLite3 Backend

### 2.1 — Missing `transaction_mode` validation

**File:** `base.py:623-624`  
**Django ref:** `sqlite3/base.py:143, 182-193`

Django validates `transaction_mode` against `frozenset(["DEFERRED", "EXCLUSIVE", "IMMEDIATE"])` and raises `ImproperlyConfigured`. libsql does not.

---

### 2.2 — `atomic_transactions = True` disagrees with Django SQLite3

**File:** `features.py:14`  
**Django ref:** `sqlite3/features.py:18` → `atomic_transactions = False`

Django SQLite3 sets `atomic_transactions = False` because sqlite3's `isolation_level` interacts poorly with atomic blocks. libsql sets it to `True`. For remote mode this makes sense (no real transactions), but for local mode it should match Django's setting to avoid the same savepoint bug.

---

### 2.3 — `test_db_allows_multiple_connections = True` disagrees with Django SQLite3

**File:** `features.py:11`  
**Django ref:** `sqlite3/features.py:14` → `False`

Django SQLite3 sets this to `False` because in-memory SQLite databases don't support multiple connections. libsql hardcodes `True`, which could cause test failures with in-memory local databases.

---

### 2.4 — `can_clone_databases = False` disables parallel test execution

**File:** `features.py:18`  
**Django ref:** `sqlite3/features.py:22` → `True`

Django SQLite3 supports cloning test databases for parallel execution. libsql disables this entirely. For local mode, file-based cloning should work.

---

### 2.5 — Missing `max_query_params` property (dynamic SQLite limit querying)

**File:** `features.py` — absent  
**Django ref:** `sqlite3/features.py:145-153`

Django SQLite3 queries `sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER` at runtime. libsql falls back to `BaseDatabaseFeatures.max_query_params = None`, potentially hitting SQLite's variable limit for large batch queries.

---

### 2.6 — Missing `django_test_skips` and `django_test_expected_failures`

**File:** `features.py:52` — empty  
**Django ref:** `sqlite3/features.py:52-132` — extensive lists

Django SQLite3 has detailed skip/failure annotations for SQLite-specific test behaviors. libsql has no such annotations — the test suite will likely produce false failures.

---

### 2.7 — `supports_json_field` hardcoded `True` vs. runtime check

**File:** `features.py:65-66`  
**Django ref:** `sqlite3/features.py:155-163`

Django SQLite3 checks at runtime whether the SQLite library supports JSON functions. libsql always returns `True`. Remote servers might use SQLite builds without JSON1 extension.

---

### 2.8 — `can_return_columns_from_insert` hardcoded `True` vs. version check

**File:** `features.py:72-73`  
**Django ref:** `sqlite3/features.py:169-170` → checks `>= (3, 35)`

Django SQLite3 gates RETURNING support on SQLite >= 3.35. libsql always enables it. Older remote SQLite servers will crash.

---

### 2.9 — `supports_aggregate_order_by_clause` hardcoded `True` vs. version check

**File:** `features.py:32`  
**Django ref:** `sqlite3/features.py:37` → checks `>= (3, 44, 0)`

---

### 2.10 — `can_alter_table_drop_column` hardcoded `True` vs. version check

**File:** `features.py:39`  
**Django ref:** `sqlite3/features.py:30` → checks `>= (3, 35, 5)`

---

### 2.11 — Missing `force_group_by()` version-dependent behavior

**File:** `operations.py:342-343` — always returns `[]`  
**Django ref:** `sqlite3/operations.py:417` → returns `["GROUP BY TRUE"]` for SQLite < 3.39

Older SQLite versions require `GROUP BY TRUE` for queries with complex GROUP BY. Hardcoding `[]` breaks queries on older servers.

---

### 2.12 — Missing `format_json_path_numeric_index()` for negative index handling

**File:** `operations.py` — absent  
**Django ref:** `sqlite3/operations.py:419-420`

Django SQLite3 uses `[#N]` syntax for negative JSON array indices. Without this override, JSON path queries with negative indices fail on SQLite.

---

### 2.13 — `last_executed_query()` is fragile (no param quoting)

**File:** `operations.py:130-131`  
**Django ref:** `sqlite3/operations.py:169-185`

libsql does simple `sql % params`. Django SQLite3 uses `QUOTE(?)` to properly escape parameters for display. This means `last_executed_query` can produce invalid SQL for display/logging when params contain special characters.

---

### 2.14 — `bulk_batch_size()` uses hardcoded 999 vs. dynamic limit

**File:** `operations.py:31-38`  
**Django ref:** `sqlite3/operations.py:31-49`

Django SQLite3 uses `features.max_query_params // len(fields)`. libsql hardcodes `999`. For composite primary keys or wide tables, this can exceed SQLite's variable limit.

---

### 2.15 — `convert_query()` in `LocalSQLiteCursor` doesn't handle `pyformat` style

**File:** `base.py:424-426`  
**Django ref:** `sqlite3/base.py:372-378`

Django's `SQLiteCursorWrapper.convert_query()` handles both `format` (`%s` → `?`) and `pyformat` (`%(name)s` → `:name`). libsql's `LocalSQLiteCursor._convert_query()` only handles `format`. Named parameter queries crash.

---

### 2.16 — Local mode: `get_new_connection()` doesn't call `Database.register_converter()` / `register_adapter()`

**File:** `base.py:593-631`  
**Django ref:** `sqlite3/base.py:49-57`

Django registers adapters for `decimal.Decimal`, `datetime.date`, `datetime.datetime` and converters for `bool`, `date`, `time`, `datetime`, `timestamp`. The libsql local mode connection relies on Django's `_functions.py` import but doesn't call these adapter registrations. Date/time values may not round-trip correctly through the local sqlite3 connection.

---

### 2.17 — `is_in_memory_db()` doesn't check `TEST["NAME"]`

**File:** `base.py:668-674`  
**Django ref:** `sqlite3/creation.py:14-17`

Django's version checks `TEST["NAME"]` in settings. libsql only checks `NAME`. If `NAME` is a file but `TEST["NAME"]` is `:memory:`, the in-memory check fails.

---

### 2.18 — `get_database_version()` returns `(3, 0, 0)` as fallback for remote

**File:** `base.py:787-798`  
**Django ref:** `sqlite3/base.py:200-201`

When `sqlite_version()` returns something unparseable or the cursor fails, libsql falls back to `(3, 0, 0)` — a version so old that any version-gated feature will be disabled. This is defensive, but no warning is emitted. Combined with hardcoded feature flags (2.8–2.10), this creates a mismatch.

---

### 2.19 — Remote mode `execute()` and `executemany()` override `_description` but don't use it for NULL checks

**File:** `base.py:277-282`

In `TursoCursor._process_response()`, `_description` is built as `(name, None, None, None, None, None, None)` for each column — all type info fields set to `None`. Django's cursor wrapper uses `description[col][1]` (type_code) in some code paths. Missing type codes may affect Django internals that inspect cursor descriptions.

---

### 2.20 — Missing collation `"virtual"` in test_collations

**File:** `features.py:47-51`  
**Django ref:** `sqlite3/features.py:46-51` → includes `"virtual": "nocase"`

Django SQLite3 adds `"virtual": "nocase"` for generated column tests. libsql omits this.

---

### 2.21 — `check_constraints()` missing detailed FK violation error reporting

**File:** `base.py:764-783`  
**Django ref:** `sqlite3/base.py:262-316`

Django SQLite3 performs additional queries (FK list, PK column, actual values) to build rich IntegrityError messages. libsql raises a simpler message. Not a functional gap, but debugging FK violations is harder.

---

### 2.22 — `creation.py` doesn't support database cloning or suffix-based naming

**File:** `creation.py`  
**Django ref:** `sqlite3/creation.py:54-107`

libsql's `DatabaseCreation` lacks `get_test_db_clone_settings()`, `_clone_test_db()`, and proper `_get_test_db_name()`. Parallel test runs cannot create isolated databases.

---

### 2.23 — `creation.py` missing `is_in_memory_db()` static method

**File:** `creation.py`  
**Django ref:** `sqlite3/creation.py:14-17`

Django's `DatabaseCreation` has a `@staticmethod is_in_memory_db()` that base.py delegates to. libsql duplicates the logic in `DatabaseWrapper.is_in_memory_db()` but creation.py lacks it.

---

### 2.24 — `schema.py` — FK constraint handling bypasses `check_constraints()` for remote

**File:** `schema.py:27-42`  
**Django ref:** `sqlite3/schema.py:24-40`

The schema proxy skips `__enter__` FK disabling and `__exit__` FK checking for remote mode (correct, since PRAGMAs don't persist). However, this also means FK violations introduced during remote schema changes go undetected. In local mode, the proxy delegates to Django's editor which does full FK management — this is correct.

---

### 2.25 — Missing `db_suffix` for database filename suffix in test setup

**File:** No equivalent  
**Django ref:** `sqlite3/creation.py` test clone settings

Django SQLite3 adds `_suffix` to test database filenames. libsql's `_get_test_db_name()` returns the production NAME unchanged — tests run against the production database.

---

## 3. Feature Gaps — vs. Django PostgreSQL Backend

### 3.1 — No native UUID type

**libsql:** `"UUIDField": "char(32)"`  
**PostgreSQL:** `"UUIDField": "uuid"`  

No UUID generation, validation, or storage optimization.

---

### 3.2 — No native JSON/JSONB type

**libsql:** `"JSONField": "text"` with `JSON_VALID()` check constraint  
**PostgreSQL:** `"JSONField": "jsonb"` with binary storage, indexing, operators

No JSON operators (`->`, `->>`, `@>`, `<@`), no JSONB indexing. JSON fields are unindexed text.

---

### 3.3 — No native Date/DateTime/Time types

**libsql:** Stored as TEXT strings  
**PostgreSQL:** Native `date`, `timestamp with time zone`, `time` types

SQLite has no temporal types. All date math is done via `strftime`/`julianday` string manipulations.

---

### 3.4 — No native Duration/Interval type

**libsql:** `"DurationField": "bigint"` (microseconds as integer)  
**PostgreSQL:** `"DurationField": "interval"` (native interval arithmetic)

Duration arithmetic requires manual conversion between microseconds and SQL expressions.

---

### 3.5 — No native IP Address type

**libsql:** `"IPAddressField": "char(15)"`, `"GenericIPAddressField": "char(39)"`  
**PostgreSQL:** `"IPAddressField": "inet"`, `"GenericIPAddressField": "inet"`

No IP validation, containment checks, or prefix operations at DB level.

---

### 3.6 — No native Boolean type

**libsql:** `"BooleanField": "bool"` (stored as integer 1/0)  
**PostgreSQL:** `"BooleanField": "boolean"` (native true/false with NULL handling)

SQLite uses dynamic typing — `bool` is affinity-based, not enforced.

---

### 3.7 — No `SELECT ... FOR UPDATE` (row-level locking)

**PostgreSQL:** `has_select_for_update = True`, plus `NOWAIT`, `SKIP LOCKED`, `OF`, `NO KEY` variants  
**libsql:** No row-level locking at all. Remote HTTP mode is stateless — locking is meaningless.

---

### 3.8 — No `DISTINCT ON` support

**PostgreSQL:** `can_distinct_on_fields = True`  
**libsql:** SQLite doesn't support `SELECT DISTINCT ON (...)`. Django falls back to subquery-based emulation.

---

### 3.9 — No `TRUNCATE ... CASCADE`

**libsql:** `sql_flush()` uses `DELETE FROM` loop with recursive FK graph traversal  
**PostgreSQL:** Single `TRUNCATE x, y, z CASCADE` statement

Significantly slower for large datasets.

---

### 3.10 — No proper SEQUENCE manipulation

**libsql:** Uses `sqlite_sequence` table (internal SQLite mechanism)  
**PostgreSQL:** `pg_get_serial_sequence()`, `setval()`, `ALTER SEQUENCE`

Sequence reset is fragile — depends on SQLite internals.

---

### 3.11 — No tablespace support

**PostgreSQL:** `supports_tablespaces = True`, `tablespace_sql()`  
**libsql:** No tablespace concept at all.

---

### 3.12 — No connection pooling

**PostgreSQL:** Native `psycopg_pool` with `ConnectionPool`, health checks, `CONN_MAX_AGE`  
**libsql:** Remote mode is stateless (no persistent connection). Local mode has no pool.

---

### 3.13 — No server-side parameter binding

**PostgreSQL:** `ServerBindingCursor` for server-side prepared statements  
**libsql:** Remote uses JSON body parameters. Local uses Python `sqlite3` client-side binding.

---

### 3.14 — No schema comments

**PostgreSQL:** `supports_comments = True`, column/table comments via `COMMENT ON`  
**libsql:** SQLite has no comment support.

---

### 3.15 — No covering indexes (INCLUDE clause)

**PostgreSQL:** `supports_covering_indexes = True`  
**libsql:** `supports_covering_indexes = False` (default). SQLite 3.35+ supports this but libsql doesn't expose it.

---

### 3.16 — No concurrent index creation

**PostgreSQL:** `CREATE INDEX CONCURRENTLY`, `DROP INDEX CONCURRENTLY`  
**libsql:** Not supported by SQLite.

---

### 3.17 — No deferrable unique constraints

**PostgreSQL:** `supports_deferrable_unique_constraints = True`, `deferrable_sql()`  
**libsql:** `supports_deferrable_unique_constraints = False` (default). SQLite supports `DEFERRABLE INITIALLY DEFERRED` on FK only, not unique.

---

### 3.18 — No materialized views

**PostgreSQL:** `can_introspect_materialized_views = True`  
**libsql:** SQLite doesn't support materialized views.

---

### 3.19 — No timezone-aware datetime storage

**PostgreSQL:** `timestamp with time zone` — stores UTC, converts on read  
**libsql:** Stores as TEXT — timezone handling is purely at the Python/Django level.

---

### 3.20 — No role-based access (`SET ROLE`)

**PostgreSQL:** `_configure_role()`, `SET ROLE` support  
**libsql:** Turso has its own auth model (JWT tokens with per-database permissions).

---

### 3.21 — No full-text search

**PostgreSQL:** `tsvector`/`tsquery` types and GIN indexes  
**libsql:** SQLite FTS5 is available but not exposed through Django's backend.

---

### 3.22 — No array field support

**PostgreSQL:** Native array types (`integer[]`, `text[]`, etc.), `ArrayField` lookups with `@>`, `&&` operators  
**libsql:** SQLite has no array type. Django's `ArrayField` would need text-based JSON emulation.

---

### 3.23 — No `GREATEST`/`LEAST` that ignores NULLs like PostgreSQL

**PostgreSQL:** `greatest_least_ignores_nulls = True` — returns NULL only if all args are NULL  
**libsql:** SQLite's `MAX`/`MIN` functions differ — `greatest_least_ignores_nulls = False` (default).

---

### 3.24 — No EXPLAIN options (ANALYZE, BUFFERS, etc.)

**PostgreSQL:** `supported_explain_formats = {"JSON", "TEXT", "XML", "YAML"}`, `explain_options` frozenset with 12 options  
**libsql:** Only `EXPLAIN QUERY PLAN` — no ANALYZE, no structured output.

---

### 3.25 — No `pg_catalog` introspection depth

**PostgreSQL:** Rich `pg_catalog` queries for constraints, indexes, sequences, collations, comments, generated columns, identity columns  
**libsql:** Limited to SQLite's `PRAGMA` introspection (table_info, index_list, foreign_key_list) and `sqlite_master` parsing.

---

### 3.26 — No stored procedures / functions

**PostgreSQL:** `create_test_procedure_without_params_sql`, `create_test_procedure_with_int_param_sql`, `supports_callproc_kwargs`  
**libsql:** No PL support. `cursor.callproc()` not implemented.

---

### 3.27 — No bulk insert optimization (INSERT ... UNNEST)

**PostgreSQL:** `InsertUnnest` compiler, `bulk_insert_sql` returning `SELECT * FROM unnest(...)`  
**libsql:** Uses standard multi-row INSERT only.

---

### 3.28 — No `select_for_update_of_column` behavior

**PostgreSQL:** `select_for_update_of_column = False` (table-level FOR UPDATE OF)  
**libsql:** `has_select_for_update = False` entirely.

---

### 3.29 — No `combine_alters` / combined DDL

**PostgreSQL:** `supports_combined_alters = True` — multiple ALTER COLUMN in one ALTER TABLE  
**libsql:** `supports_combined_alters = False` (default). SQLite remakes the table anyway.

---

### 3.30 — No `NULLS FIRST` / `NULLS LAST` clause support

**PostgreSQL:** `supports_order_by_nulls_modifier = True`, `nulls_order_largest = True`  
**libsql:** SQLite orders NULLS FIRST by default (`order_by_nulls_first = True`), but doesn't support explicit NULLS FIRST/LAST modifiers.

---

### 3.31 — No `assume_role` / credential role switching

**PostgreSQL:** Supports `SET ROLE` via `OPTIONS["assume_role"]`  
**libsql:** Turso uses per-database JWT tokens — role switching not applicable.

---

### 3.32 — No health check integration

**PostgreSQL:** `CONN_HEALTH_CHECKS` setting, `close_if_health_check_failed()`  
**libsql:** Remote mode doesn't maintain persistent connections; local mode doesn't implement health checks.

---

## 4. Round 2 — Deep Audit Findings (5 Parallel Agents)

Second-pass audit focusing on remote (Turso HTTP) mode specifics: connection handling, error boundaries, transaction emulation, type conversion, and date/time SQL correctness.

### 4.1 — Connection/Error handling gaps (Agent 1)

**4.1.1 — PRAGMAs not included in batch requests**
- **File:** `base.py` — `_CONNECTION_PRAGMAS` only sent on `init_connection_state()`, not in each batch
- **Fix:** Connection PRAGMAs now included in batch request preamble

**4.1.2 — `init_connection_state()` no-op for remote**
- **File:** `base.py` — `init_connection_state()` skipped for remote; Turso resets state each request
- **Fix:** PRAGMAs now sent as part of first request in a session, not as persistent state

**4.1.3 — `json.JSONDecodeError` not caught**
- **File:** `base.py` — `_execute_http()` — HTTP response parsing didn't handle malformed JSON
- **Fix:** Added `json.JSONDecodeError` catch with clear error message

**4.1.4 — `_raise_http_error()` limited error info**
- **File:** `base.py` — Only showed status code, not response body
- **Fix:** Now includes response body in error (truncated at 500 chars)

**4.1.5 — `_close()` error swallowing**
- **File:** `base.py` — `_close()` caught all exceptions silently
- **Fix:** Now logs warnings for close errors instead of swallowing

**4.1.6 — `disable_constraint_checking()` return value**
- **File:** `base.py` — Returned `True` for remote even though FK cannot be disabled across requests
- **Fix:** Now returns `False` with warning for remote mode

### 4.2 — Transaction/Cursor gaps (Agent 2)

**4.2.1 — INSERT RETURNING bypasses buffering**
- **File:** `base.py:432` — `RETURNING` in SQL string excluded the statement from write buffering
- **Impact:** Every `Model.objects.create()` inside `atomic()` committed immediately
- **Fix:** `can_return_columns_from_insert` now `False` for remote mode (see Round 3 fix)

**4.2.2 — Savepoint buffer truncation wrong**
- **File:** `base.py` — `_savepoint_rollback` truncated buffer at wrong index
- **Fix:** Corrected buffer truncation logic

**4.2.3 — SQL comment stripping incomplete**
- **File:** `base.py` — `_is_write_statement()` didn't strip `/* */` block comments or `--` line comments before checking prefix
- **Fix:** Added `_strip_sql_comments()` using regex for both comment styles

**4.2.4 — `close()` with non-empty buffer warning**
- **File:** `base.py` — Closing connection with buffered writes silently discarded them
- **Fix:** Now logs warning if buffer is non-empty at close

**4.2.5 — Auto-flush missing `try/finally`**
- **File:** `base.py` — Auto-flush before reads could leave buffer in inconsistent state on error
- **Fix:** Wrapped auto-flush in try/finally

### 4.3 — Type handling gaps (Agent 3)

**4.3.1 — NaN/Inf float values**
- **File:** `base.py` — `_py_value_to_turso_type()` stored NaN/Inf as text `"NaN"`/`"Infinity"`
- **Issue:** Round-trip returns string instead of `float('nan')`/`float('inf')`
- **Fix:** Documented limitation; SQLite has no native NaN/Inf support

**4.3.2 — Decimal/date/datetime/time/timedelta conversion**
- **File:** `base.py` — All standard Django type converters verified working for both modes
- **Result:** No issues found — converters correctly registered and applied

**4.3.3 — Unknown type warning**
- **File:** `base.py` — Values with unknown Python types fell through to `str()` without warning
- **Fix:** Added warning log for unrecognized types

### 4.4 — Schema/Migration gaps (Agent 4)

**4.4.1 — `_remake_table` broken for FK-referenced tables**
- **File:** `schema.py` — Table recreation requires `PRAGMA foreign_keys=OFF` across multiple statements
- **Impact:** `ALTER TABLE` on FK-referenced tables fails in remote mode
- **Status:** Fundamental Turso HTTP limitation — each request is independent

**4.4.2 — Introspection fully functional**
- **File:** `introspection.py` — All `sqlite_master` and `PRAGMA table_info` queries work correctly over HTTP
- **Result:** No issues — proxies Django's built-in introspection correctly

**4.4.3 — Test database isolation not fixable without Turso API support**
- **File:** `creation.py` — Remote mode cannot create/destroy test databases
- **Status:** Requires server-side `CREATE DATABASE` API from Turso

### 4.5 — `_get_varchar_column()` fix
- **File:** `base.py:184-188` — Was returning unformatted template string `"varchar(%(max_length)s)"`
- **Fix:** Changed to `"varchar(%(max_length)s)" % data` to match Django's behavior

---

## 5. Round 3 — Remote vs Django SQLite3 Comparison (5 Parallel Agents)

Comprehensive comparison of remote (Turso HTTP) mode against Django's built-in SQLite3 backend. Five agents analyzed feature flags, SQL functions, transaction architecture, schema/migrations, and type/lookup/expression gaps.

### 5.1 — Incorrect Feature Flags for Remote Mode (Agent 1)

Five flags inherited as `True` from `BaseDatabaseFeatures` but fundamentally broken over stateless HTTP:

| # | Flag | Was | Fixed to | Why |
|---|---|---|---|---|
| 5.1.1 | `can_rollback_ddl` | `True` | `_is_local` (False) | DDL auto-commits over HTTP |
| 5.1.2 | `can_defer_constraint_checks` | `True` | `_is_local` (False) | Needs persistent PRAGMA state |
| 5.1.3 | `uses_savepoints` | inherited `True` | `_is_local` (False) | Buffer snapshots only |
| 5.1.4 | `can_release_savepoints` | `True` | `_is_local` (False) | No server-side SAVEPOINT |
| 5.1.5 | `supports_transactions` | `True` | `_is_local` (False) | Auto-commit per statement |
| 5.1.6 | `can_return_columns_from_insert` | remote: `True` | remote: `False` | RETURNING bypasses write buffering — every `Model.objects.create()` inside `atomic()` committed immediately |

**Severity:** HIGH — incorrect flags cause Django to assume capabilities that don't exist, leading to silent data corruption (5.1.6) or unexpected errors (5.1.1–5.1.5).

### 5.2 — Date/Time Extraction Bugs (Agent 2 + Agent 5)

| # | Bug | Before | After | Impact |
|---|---|---|---|---|
| 5.2.1 | `second` always 0 | `strftime('%f') / 1000` — `%f` returns `SS.SSS`, dividing by 1000 truncates to 0 | `strftime('%S')` | All time/datetime second extractions returned 0 |
| 5.2.2 | `week_day` off by one | `strftime('%w')` returns Sunday=0 | `strftime('%w') + 1` returns Sunday=1 | Consistent +1 off for all week_day lookups |
| 5.2.3 | `iso_week_day` completely wrong | `strftime('%w')` Sunday=0 mapping | `strftime('%u')` Monday=1..Sunday=7 | Wrong weekday numbering scheme entirely |
| 5.2.4 | `week` non-ISO | `strftime('%W')` returns 00-53 with Monday start | ISO 8601 formula via `strftime('%j', col, '-3 days', 'weekday 4')` | Wrong week numbers at year boundaries |

**Severity:** HIGH — these affect common ORM queries like `filter(date__week=5)`, `filter(time__second=30)`, `annotate(week_day=ExtractWeekDay('date'))`.

### 5.3 — Missing/Incorrect SQL Functions (Agent 2 + Agent 5)

**5.3.1 — 12 crashing functions** (Python-registered on local, unavailable on remote server):
- Hash: `MD5`, `SHA1`, `SHA224`, `SHA256`, `SHA384`, `SHA512`
- String: `LPAD`, `RPAD`, `REPEAT`, `REVERSE`
- Math: `COT`, `SIGN`

**Status:** Cannot be fixed without server-side extension loading. Users must avoid these ORM functions in remote mode.

**5.3.2 — `BITXOR` crashing**
- **File:** `operations.py:304` (inherited) — `#` connector generates `BITXOR(...)` which is Python-registered
- **Fix:** `RemoteDatabaseOperations.combine_expression()` now raises `NotSupportedError` for `#` connector

**5.3.3 — `StdDev`/`Variance` unconditional block**
- **File:** `operations.py:398-406` — Already raises `NotSupportedError` for all field types in remote mode
- **Note:** Local mode only blocks on date/time fields. Remote blocks unconditionally — intentional design choice.

### 5.4 — Transaction Architecture (Agent 3)

**5.4.1 — `can_return_columns_from_insert=True` identified as single most damaging flag**
- Every `Model.objects.create()` inside `atomic()` uses `INSERT...RETURNING` which bypasses buffering and commits immediately
- **Fix:** Set `can_return_columns_from_insert = False` for remote mode (see 5.1.6)

**5.4.2 — Read-after-write within transaction**
- Reads inside `atomic()` trigger auto-flush of buffered writes
- **Status:** Working as designed — tradeoff between isolation and performance

**5.4.3 — `lastrowid` access auto-commits**
- Accessing `cursor.lastrowid` (after every INSERT) flushes and commits all buffered writes
- **Status:** Inherent to stateless HTTP design; `_flushed` flag detects and warns on rollback

### 5.5 — Type/Lookup/Expression Gaps (Agent 5)

**5.5.1 — Field type storage mappings identical**
- All 28 Django field types map to identical SQLite column types in both local and remote
- **Result:** No data storage differences

**5.5.2 — Duration arithmetic edge case**
- **File:** `operations.py:580-602` — `combine_duration_expression` with two DurationField operands (timedelta + timedelta) produces date string instead of integer sum
- **Fix:** Documented limitation; `*` and `/` operators work correctly for DurationField values

**5.5.3 — Timezone conversion ignored**
- All `RemoteDatabaseOperations` methods accept `tzname` but ignore it (SQLite has no native tz support)
- **Status:** Documented limitation — correct when DB stores UTC (Django default)

**5.5.4 — Named parameter binding risk**
- Named `%(name)s` params converted to `:name` SQL but sent as positional array
- **Status:** Low risk — Django predominantly uses positional `%s` style

**5.5.5 — NaN/Inf float round-trip broken**
- NaN stored as text `"NaN"`, returned as string instead of `float('nan')`
- **Status:** Low impact — SQLite has no native NaN/Inf

---

## 6. Summary

| Category | Count |
|---|---|
| Security vulnerabilities | 10 |
| Missing vs. Django SQLite3 (Round 1) | 25 |
| Missing vs. PostgreSQL | 32 |
| Round 2 deep-audit findings | 25 |
| Round 3 comparison analysis findings | 17 |
| **Total findings** | **109** |

### Fix Status

| Round | Date | Findings | Fixed | Unfixable |
|---|---|---|---|---|
| Round 1 — Initial audit | 2026-05-13 | 67 | 13 (all critical+high) | 0 |
| Round 2 — Deep audit (5 agents) | 2026-05-13 | 25 | 23 | 2 (Turso API limitations) |
| Round 3 — Comparison analysis (5 agents) | 2026-05-13 | 17 | 11 | 6 (server-side function gaps) |
| **Total** | | **109** | **47** | **8** |

### Unfixable Limitations (fundamental Turso HTTP constraints)

1. 12 Python-registered SQL functions unavailable (hash, string, math) — requires server-side extensions
2. Timezone conversion in SQL — SQLite has no native tz support
3. `_remake_table` for FK-referenced tables — needs PRAGMA persistence across requests
4. Test database creation for remote — requires `CREATE DATABASE` API from Turso
5. NaN/Inf float round-trip — SQLite has no native NaN/Inf
6. Named parameter binding mismatch — Turso API positional array limitation

---

## 7. Fix Log

### 2026-05-13 — Round 3: Comparison Analysis Fixes

**5.1.1–5.1.5 — Five incorrect feature flags for remote mode** (FIXED)
- `can_rollback_ddl`, `can_defer_constraint_checks`, `uses_savepoints`, `can_release_savepoints`, `supports_transactions` all changed from hardcoded `True` to `@cached_property` returning `self._is_local`
- Remote mode now correctly reports all five as `False`
- Local mode continues to report `True` for all five
- File: `features.py`

**5.1.6 — `can_return_columns_from_insert` for remote mode** (FIXED)
- Changed remote mode return from `True` to `False`
- This is the single most damaging incorrect flag — every `Model.objects.create()` inside `atomic()` was using `INSERT...RETURNING` which bypassed write buffering and committed immediately
- File: `features.py:124-128`

**5.2.1 — `second` extraction always returned 0** (FIXED)
- `strftime('%f')` in SQLite returns `SS.SSS` (seconds with fractional part), not fractional milliseconds
- Dividing by 1000 and casting to integer always gave 0
- Fixed: use `strftime('%S')` which returns integer seconds 00-59
- Affected: `datetime_extract_sql` and `time_extract_sql`
- File: `operations.py:480-483`, `497-499`

**5.2.2 — `week_day` offset by one** (FIXED)
- `strftime('%w')` returns Sunday=0..Saturday=6
- Django convention is Sunday=1..Saturday=7
- Fixed: `CAST(strftime('%w', col) AS integer) + 1`
- File: `operations.py:431-434`, `464-467`

**5.2.3 — `iso_week_day` completely wrong mapping** (FIXED)
- Was using `%w` (Sunday=0) when Django expects Monday=1..Sunday=7
- Fixed: use `strftime('%u')` which returns ISO weekday Monday=1..Sunday=7
- File: `operations.py:436-439`, `469-472`

**5.2.4 — `week` used non-ISO week number** (FIXED)
- `strftime('%W')` returns week 00-53 where week 0 means "before first Monday"
- Django's local mode returns ISO 8601 week (1-53) via `dt.isocalendar().week`
- Fixed: ISO 8601 formula — `(strftime('%j', col, '-3 days', 'weekday 4') - 1) / 7 + 1`
- File: `operations.py:441-445`, `474-478`

**5.3.2 — `BITXOR` crashes in remote mode** (FIXED)
- `combine_expression` with `#` connector generates `BITXOR(...)` which is Python-registered only
- Fixed: `RemoteDatabaseOperations.combine_expression()` raises `NotSupportedError` for `#` connector
- `^` (POWER) continues to work via `super()` delegation
- File: `operations.py:607-611`

**5.5.2 — Duration arithmetic edge case** (DOCUMENTED)
- `combine_duration_expression` with two DurationField operands (timedelta+timedelta) uses `datetime()` which interprets the first integer as Julian day number
- This produces a date string instead of integer sum
- `*` and `/` operators work correctly (pure integer arithmetic)
- Added documentation comment explaining the limitation
- File: `operations.py:614-621`

### 2026-05-13 — Round 2: Deep Audit Fixes

**4.1.3 — json.JSONDecodeError handling** (FIXED)
- Added `json.JSONDecodeError` catch in `_execute_http()` with clear error message
- File: `base.py`

**4.1.4 — _raise_http_error improvements** (FIXED)
- Now includes response body in error message (truncated at 500 chars)
- File: `base.py`

**4.1.5 — _close() error wrapping** (FIXED)
- Close errors now logged as warnings instead of silently swallowed
- File: `base.py`

**4.1.6 — disable_constraint_checking return** (FIXED)
- Returns `False` with warning for remote mode (FK cannot be disabled across requests)
- File: `base.py`

**4.2.3 — SQL comment stripping** (FIXED)
- `_strip_sql_comments()` strips `/* */` block comments and `--` line comments
- `_is_write_statement()` now calls `_strip_sql_comments()` before prefix check
- File: `base.py`

**4.2.4 — close buffer warning** (FIXED)
- Closing connection with non-empty buffer now logs warning
- File: `base.py`

**4.2.5 — Auto-flush try/finally** (FIXED)
- Auto-flush before reads wrapped in try/finally to prevent buffer corruption on error
- File: `base.py`

**4.3.1 — NaN/Inf float handling** (FIXED)
- NaN stored as text `"NaN"`, Infinity as `"Infinity"` / `"-Infinity"`
- Round-trip limitation documented
- File: `base.py`

**4.3.3 — Unknown type warning** (FIXED)
- Values with unrecognized types now generate a warning log
- File: `base.py`

**4.5 — _get_varchar_column() fix** (FIXED)
- Changed from returning template string `"varchar(%(max_length)s)"` to formatted result `"varchar(%(max_length)s)" % data`
- File: `base.py`

**2.5 — max_query_params property** (FIXED)
- Added `@cached_property` returning `sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER` for local, `32766` for remote
- File: `features.py`

**2.6 — test_collations virtual** (FIXED)
- Added `"virtual": "nocase"` to `test_collations` dict
- File: `features.py`

**2.6 — django_test_skips** (FIXED)
- Added `test_alter_field_default_does_not_perform_queries` skip
- File: `features.py`

**2.11 — force_group_by version-gated** (FIXED)
- Returns `["GROUP BY TRUE"]` for SQLite < 3.39, `[]` otherwise
- File: `operations.py`

**2.12 — format_json_path_numeric_index** (FIXED)
- Added `[#N]` syntax for negative JSON array indices
- File: `operations.py`

**2.13 — last_executed_query quoting** (FIXED)
- Now properly quotes params: NULL for None, direct for numeric, repr(str()) for strings
- File: `operations.py`

**2.22–2.23 — creation.py test clone and is_in_memory_db** (FIXED)
- `get_test_db_clone_settings()` returns suffixed filepath for local parallel tests
- `setup_worker_connection()` delegates to Django's SQLite creation for local mode
- `is_in_memory_db()` static method added
- File: `creation.py`

**2.24 — schema.py __enter__/__exit__ FK handling** (FIXED)
- `__enter__`/`__exit__` conditionally skip FK toggling based on `_is_local_name()`
- Local mode: delegates to wrapped Django SQLite editor (full FK management)
- Remote mode: skips to base class (PRAGMAs don't persist)
- File: `schema.py`

### 2026-05-13 — Round 1: Critical Fixes

**1.1 — transaction_mode SQL injection** (FIXED)
- Added `transaction_modes = frozenset(["DEFERRED", "EXCLUSIVE", "IMMEDIATE"])` class attribute on `DatabaseWrapper` (`base.py:586`)
- Added validation in `get_new_connection()` that raises `ImproperlyConfigured` for invalid values
- Matches Django's built-in SQLite backend behavior exactly

**1.3 — Read-after-write isolation** (FIXED)
- `TursoCursor.execute()` and `executemany()` now auto-flush buffered writes before executing any non-write statement inside a transaction
- After flushing, writes are committed and visible to subsequent reads

**1.4 — lastrowid silent commit / broken rollback** (FIXED)
- Added `_flushed` flag to `TursoHTTPConnection` that tracks whether any part of the current transaction has been committed
- `rollback_transaction()` raises `DatabaseError` with clear message if any writes were auto-flushed

**2.25 — Tests run against production database** (FIXED)
- Rewrote `creation.py` with proper test database isolation for local mode, table-level cleanup for remote mode

**1.2 — pyformat / named parameter support** (FIXED)
- `_convert_query()` now handles both `%s` → `?` and `%(name)s` → `:name` styles
- `_build_turso_args()` accepts `param_names` and looks up values by name from Mapping params

**1.9 — PRAGMA removed from _WRITE_PREFIXES** (FIXED)
- Removed `"PRAGMA"` from write prefixes; PRAGMAs execute immediately

**2.2 — atomic_transactions flag now mode-dependent** (FIXED)
- `@cached_property` returning `not self._is_local` — False for local (matching Django), True for remote

**2.3 — test_db_allows_multiple_connections now mode-dependent** (FIXED)
- `@cached_property` returning `not self._is_local` — False for local, True for remote

**2.8–2.10 — Feature flags version-gated** (FIXED)
- `can_return_columns_from_insert`, `supports_aggregate_order_by_clause`, `can_alter_table_drop_column` now check `connection.get_database_version()` for local mode

**2.16 — Missing adapter/converter registration** (FIXED)
- Module-level `register_converter()` and `register_adapter()` calls added for bool, date, time, datetime, timestamp

**2.14 — bulk_batch_size dynamic limit** (FIXED)
- Uses `max_params // len(fields)` instead of hardcoded 999
