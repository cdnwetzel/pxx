# Contributing to pxx

Thanks for your interest! pxx is a personal, offline-capable aider orchestrator —
contributions that keep it simple, safe, and sovereign are welcome.

## Setup
```bash
git clone https://github.com/cdnwetzel/pxx && cd pxx
uv sync --extra dev          # creates .venv on a supported Python (3.11/3.12)
uv run pytest -q             # suite is green
uv run ruff check && uv run ruff format --check
```
> Local Python 3.13+ is unsupported (aider pins `<3.13`); `uv` selects a managed
> 3.12 for you. Don't `pip install` the project into a 3.13 system interpreter.

## Ground rules
- **Read `CONVENTIONS.md` and `CLAUDE.md`** — they define the code style and the
  repo's guardrails: `pyproject.toml`, `config/*.yml`, and `.aiderignore` are
  hand-edit-only, and `.github/workflows/` is trust-boundary protected.
- Keep the suite green under **both** a real and a sterile `$HOME` (tests must
  not depend on your personal `~/.config/pxx/env`).
- Plan/status changes land in the **same commit** as the work
  (`plans/backlog.md` hygiene).
- `aider-chat` is exact-pinned by design — bump only via the discipline in
  `CLAUDE.md`.

## Pull requests
Small, focused PRs with a clear rationale and green CI. Say what changed and
why, and link any related plan in `plans/`.
