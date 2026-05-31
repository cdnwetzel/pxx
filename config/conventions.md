# Conventions

Drop this into a project root as `CONVENTIONS.md`. Aider auto-reads it.

## Stack

- Python 3.11+
- `uv` for environment + dependencies
- `ruff` for lint + format (replaces black, isort, flake8)
- `pytest` for tests
- `mypy --strict` for types

## Layout

- `src/<package>/` for code
- `tests/` mirrors `src/` layout
- `pyproject.toml` only — no `setup.py`, no `requirements.txt`

## Style

- Type hints on every public signature
- Named imports; never `from foo import *`
- `dataclass` or `TypedDict` over `dict[str, Any]`
- `pathlib.Path` over `os.path`
- No docstrings on internal functions; one-line on public ones

## Workflow

- `ruff check --fix && ruff format` before commit
- `pytest` before push
- Tests live next to source path: `src/x/y.py` → `tests/x/test_y.py`
- Commit messages: imperative mood, no trailing period
