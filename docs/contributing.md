# Contributing

## Development Setup

### Prerequisites

- Python 3.10+
- pip
- Git

### Clone and Install

```bash
git clone https://github.com/CyberCalculus/django-libsql-backend.git
cd django-libsql-backend
pip install -e .
```

### Verify Installation

```bash
python -c "import django_libsql; print(django_libsql.__version__)"
```

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feature/your-feature
```

### 2. Make Changes

- Follow existing code style
- Add docstrings to public methods
- Keep changes focused and minimal

### 3. Test

```bash
# Run Django system checks
python manage.py check

# Run migrations
python manage.py migrate

# Test basic ORM operations
python manage.py shell
```

### 4. Commit

```bash
git add .
git commit -m "Description of change"
```

### 5. Push and Create PR

```bash
git push origin feature/your-feature
```

Create a pull request on GitHub.

## Code Style

### Python

- Follow PEP 8
- Use type hints on public methods
- Keep functions focused and small
- Add docstrings to public APIs

### Docstrings

Use Google-style docstrings:

```python
def method(self, arg1, arg2):
    """Short description.

    Longer description if needed.

    Args:
        arg1: Description of arg1.
        arg2: Description of arg2.

    Returns:
        Description of return value.

    Raises:
        SomeError: When something goes wrong.
    """
```

### Comments

- Explain **why**, not **what**
- Keep comments up-to-date
- Remove stale comments

## Testing

### Local Testing

Integration tests run via a companion Django project at `../dbpytursomodule/`:

```bash
cd ../dbpytursomodule
python manage.py check
python manage.py migrate
python manage.py shell
```

The companion project uses `django_libsql` as its database backend and serves as the integration test suite.

### With Turso (requires AUTH_TOKEN)

```bash
TURSO_DB_URL=https://your-db.turso.io \
TURSO_AUTH_TOKEN=your-token \
python manage.py test
```

### Test Database

The backend supports Django's test database isolation:

- **Local mode**: Creates a separate test file
- **Remote mode**: Drops all tables between test runs
- Supports parallel test execution via `_clone_test_db`

## Architecture

See [Architecture Deep Dive](architecture.md) for internals.

### Key Files

| File | Purpose |
|---|---|
| `base.py` | Main backend logic, cursors, HTTP transport, Hrana support |
| `operations.py` | SQL generation |
| `features.py` | Feature flags |
| `schema.py` | Schema editor proxy |
| `introspection.py` | Database introspection proxy |
| `creation.py` | Test database management |
| `client.py` | CLI: dbshell command |
| `functions.py` | Custom DB functions (local mode) |

## Release Process

### Version Bump

1. Update `__version__` in `django_libsql/__init__.py`
2. Update version in `pyproject.toml`
3. Update `CHANGELOG.md`

### Build and Publish

```bash
rm -rf dist build *.egg-info
python -m build
twine upload dist/*
```

### Tag Release

```bash
git tag v0.1.2
git push origin v0.1.2
```

CI will automatically build and publish to PyPI on tag push.

## Reporting Issues

When reporting issues, include:

1. **Python version**: `python --version`
2. **Django version**: `python -c "import django; print(django.VERSION)"`
3. **Backend version**: `python -c "import django_libsql; print(django_libsql.__version__)"`
4. **Full error traceback**
5. **Minimal reproduction code**
6. **Database configuration** (with sensitive values removed)

## Code of Conduct

Be respectful and constructive. We're all here to build something useful.
