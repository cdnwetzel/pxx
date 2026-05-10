# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`pxx` is a thin Python CLI wrapper around [aider](https://aider.chat). It probes
which Ollama endpoint is reachable, picks a matching model, and `os.execv`s into
aider with a curated set of flags. Personal, offline-capable, single-developer
tool. Once aider takes over the process, pxx is out of the picture.

Two-machine design:
- **Studio** (M4 Max, 36GB) hosts Ollama with `devstral:24b` (default) and others
- **Neo** (8GB MacBook) runs `pxx` itself, with `qwen3:4b` as the offline fallback

Endpoint priority (first reachable wins, 1s probe timeout):
`PXX_OLLAMA_BASE` override → Studio LAN → Studio over VPN → Neo localhost.

## Commands

### Using pxx

```bash
# Install (editable) — uses uv tool
uv tool install --editable . --python 3.12

# Pre-flight: which endpoints are up, memory pressure, loaded models, CPU temp
bash scripts/doctor.sh

# Run pxx in another project directory
cd ~/some-python-project && pxx
```

### Developing pxx

Dev deps (`pytest`, `ruff`) live in `pyproject.toml` under
`[project.optional-dependencies] dev`. Use a project-local venv managed by uv —
**not** `--with` flags on the tool install (those bypass standard packaging).

```bash
# One-time per machine: create .venv/ from pyproject.toml + uv.lock
uv sync --extra dev

# Lint
uv run ruff check --fix
uv run ruff format

# Test
uv run pytest -q
```

Tests cover the pure helper functions in `pxx/cli.py` and `pxx/endpoints.py`
(`model_for`, `_in_git_repo`, `_find_aider`, `_probe`, `detect_endpoint`).
They do **not** exercise aider or Ollama — those are integration concerns.

After editing `cli.py` or `endpoints.py`, the running pxx/aider session still
has old code in memory — the user must exit and re-launch to test interactively.
Tests will catch regressions on the pure functions automatically.

## Architecture (the parts that span multiple files)

**`pxx/cli.py`** is the only entry point (`pxx.cli:main`). It:
1. Calls `detect_endpoint()` from `pxx/endpoints.py`
2. Sets `OLLAMA_API_BASE` from the chosen endpoint
3. Picks model via `model_for(endpoint)` — only `name == "neo"` gets `NEO_DEFAULT`,
   everything else (including the explicit `override` endpoint) gets `STUDIO_DEFAULT`.
   If overriding to a small box via `PXX_OLLAMA_BASE`, the user must also set
   `PXX_MODEL`, otherwise it will try to load `devstral:24b` (the Studio default).
4. Parses one pxx-level flag (`--edit`) out of `sys.argv`. Default is ask mode
   (read-only); `--edit` flips to code mode.
5. Calls `_build_aider_args()` which assembles the full argv, injecting
   `--chat-mode ask|code` *unless* the user has already passed `--chat-mode`
   themselves (explicit user choice wins).
6. Locates the aider binary (prefers same-venv) and `os.execv`s into it, passing
   `--read pxx/prompts/system.md`, `--config config/aider.conf.yml`,
   `--model-settings-file config/model-settings.yml`, plus `--no-git` when cwd
   is not a git repo.

## Modes — reviewer first

pxx defaults to **ask mode** (read-only). The user must pass `--edit` to allow
file changes. This makes it safe to run pxx against any codebase without risking
accidental edits. The startup banner prints the active mode.

When working on pxx itself: the same rule applies — type `pxx --edit` to make
changes. No special-casing for the pxx repo.

**Three configs feed aider, and they do different things:**
- `config/aider.conf.yml` — aider behavior (edit-format, caching, lint/test cmds, privacy)
- `config/model-settings.yml` — per-model context windows; values here are OOM-sensitive on the Studio
- `pxx/prompts/system.md` — system prompt always loaded as a read-only file into every session

`config/conventions.md` is a *template* meant to be copied into other projects
as `CONVENTIONS.md`; pxx itself does not read it. The repo-root `CONVENTIONS.md`
is the meta-rules for editing pxx with pxx.

**Slash commands** in `pxx/commands/*.md` are prompt fragments loaded inside an
aider session via `/load <path>`. They are not Python code. Editing them changes
agent behavior, not pxx behavior.

## Code review observations (`../review/*.md`)

Above this repo, at `../review/` (i.e. `/Users/you/ai/code_pro/review/`),
three different AI CLIs — **Claude Code, Gemini, and Codex** — periodically
perform **read-only** code reviews of pxx. Each reviewer tends to excel at
slightly different roles, so the multi-reviewer setup is intentional.

**Ownership is tracked in `../review/inventory.md`.** Codex owns the numbered
series (`01-overview.md` through `04-observations.md` plus `README.md`).
Gemini owns `gemini-notes.md`.

**Claude Code's role here is read-only.** This agent does not create, modify,
or stake out files in `../review/`. The user wants Claude Code's contribution
to land in the pxx codebase (code, tests, docs) — not in the review folder.
Treat `../review/` as inputs only.

When the user cites a finding ("flagged in 04-observations.md..."):
1. Locate the file in `../review/` and read the cited item in full
2. Address it by editing the relevant code/docs *in the pxx repo*, not by
   modifying the review file
3. Leave other findings alone — they may be intentional, queued, or already
   under review by another agent
4. **Never write to `../review/`** — not even to add new files. If you have an
   observation worth preserving, suggest the user surface it themselves or
   capture it in `plans/`, `CLAUDE.md`, or the relevant code comment.

The reviewers may also be **stale** — the codebase may have moved on since the
last review pass. Verify a cited finding against current code before acting.

**Stay open-minded but not credulous.** Some findings will be misguided
(reviewers don't know the user's intent or project history). Use them as
planning inputs, not commands.

**Proactively notice the same classes of drift the review docs catch:**
- README claims ↔ code defaults (model names, version pins, command examples)
- Setup scripts ↔ what they actually install
- Comments ↔ behavior
- Env-var docs ↔ env-var reads

Surface those in replies. Do not fix silently.

## Hard guardrails (enforced by `.aiderignore`)

These files must NOT be modified without explicit user request — wrong values
break installs, OOM the Studio, or alter agent behavior subtly:

- `config/model-settings.yml` (Ollama context windows)
- `config/aider.conf.yml`, `.aider.conf.yml`
- `pyproject.toml`
- `scripts/setup-studio.sh`, `scripts/setup-neo.sh`, `scripts/doctor.sh`
- `.aiderignore`, `CONVENTIONS.md`

If a task seems to require editing one of these, stop and ask. The
`CONVENTIONS.md` rule is: refuse and ask the user to do it by hand.

## Style (from `CONVENTIONS.md` and `pxx/prompts/system.md`)

- Python 3.11+, modern syntax (`match`, `|` unions, `Self`)
- Type hints on every public signature; no `Any` without reason
- stdlib first; new third-party deps need explicit justification
- `ruff format` is run after edits — don't fight its style
- No docstrings unless asked; no comments unless the *why* is non-obvious
- No try/except for control flow; no defensive code for impossible inputs
- Prefer `dataclass`/`TypedDict` over dict-of-anything; `pathlib.Path` over `os.path`
- Shell scripts: `#!/usr/bin/env bash`, `set -euo pipefail`

## Environment variables that affect behavior

| Var | Effect |
|---|---|
| `PXX_OLLAMA_BASE`      | Skip detection entirely, use this URL |
| `PXX_STUDIO_LAN_URL`   | Override `http://mac-studio.local:11434` |
| `PXX_STUDIO_REMOTE_URL`| Studio's VPN-reachable URL (empty by default) |
| `PXX_MODEL`            | Force a specific model regardless of endpoint |
