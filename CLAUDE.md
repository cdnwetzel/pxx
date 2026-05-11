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

**Security posture:** the Studio binds Ollama to `0.0.0.0:11434` (all
interfaces). Ollama itself has no authentication, so this is only safe
because the network boundary is the auth layer: trusted LAN at the office,
SSL VPN for remote access, no public-internet exposure. If the Studio
ever moves to an untrusted network, change `OLLAMA_HOST` to
`127.0.0.1:11434` first (see inline note in `scripts/setup-studio.sh`).

## Commands

### Using pxx

```bash
# Install (editable) — uses uv tool
uv tool install --editable . --python 3.12

# Pre-flight: which endpoints are up, memory pressure, loaded models, CPU temp
bash scripts/doctor.sh

# Run pxx in another project directory
cd ~/some-python-project && pxx

# Self-modes (#001 Tier 1): run pxx's own gates from anywhere
pxx --self-test       # uv run pytest -q against the pxx repo
pxx --self-lint       # ruff check + ruff format --check against the pxx repo
```

### Aider upgrade discipline

`pyproject.toml` pins `aider-chat==<exact-version>` deliberately. Aider
releases roughly weekly and can change behavior pxx depends on (chat
modes, `--read` semantics, `--model-settings-file` shape, edit format
defaults, exit codes). **Never bump on auto-pilot.**

When upgrading aider:

1. Read aider's CHANGELOG / release notes for the new version (and any
   versions skipped since the current pin).
2. Spot-check pxx's specific touch points against the new aider:
   `--chat-mode`, `--read`, `--config`, `--model-settings-file`, edit
   format, exit semantics, and the `os.execv` boundary.
3. Bump `aider-chat==<new>` in `pyproject.toml`.
4. `uv sync --extra dev` to refresh `.venv/`.
5. `uv run pytest -q` and `bash scripts/test_lib.sh` for regression.
6. `pxx --list-commands` and a real session smoke test.
7. Commit `chore(aider): bump to <new>` with a one-line summary of what
   changed in aider and which pxx touch points were verified.

Even patch releases get this treatment. The discipline is the point.

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

## Plans inventory (`plans/backlog.md`)

The `plans/` folder is governed by `plans/backlog.md` — a master inventory
where each plan has a stable numeric ID and explicit Blocks / Blocked by
columns.

**Before proposing a new plan in this repo:**
1. Read `plans/backlog.md` to make sure no existing plan covers the idea.
2. If a similar plan exists, expand it rather than creating a duplicate.
3. If a new plan is genuinely warranted, follow the "Workflow for adding a
   new plan" section in backlog.md: pick the next free ID, create
   `plans/<slug>.md`, add the `> Backlog ID: NNN` header line at the top,
   add a row, and bump the next-free-ID line.

This keeps the planning surface coherent as it grows.

### Status hygiene — non-negotiable

The backlog's status column must reflect current reality. **Update it in the
same commit as the work that motivates the change.** Never batch status
updates into a separate "housekeeping" commit; never let the backlog show
`planned` for a plan with in-flight commits.

Transitions:

- **Starting work** on a plan: `planned` → `in-progress` in the same commit
  as the first concrete code/doc change.
- **Multi-step plan**: status stays `in-progress` across all commits until
  the last verification step lands. Do not bounce back to `planned`.
- **Completing**: `in-progress` → `done` in the same commit that lands the
  last verification step.
- **Cascade unblock**: when a plan reaches `done`, scan for any plan whose
  "Blocked by" column lists this ID. Remove the now-`done` ID from that
  column. If the column becomes empty, transition `blocked` → `planned`
  (or `in-progress` if implementation starts in the same commit).
- **Newly discovered blocker**: if mid-implementation reveals a missing
  prerequisite, transition `in-progress` → `blocked` and add the new ID
  to "Blocked by". Surface this in the commit message.
- **`Next free ID`**: bump whenever a new plan is added; never let it lag.

The motivating rule: *"a backlog whose statuses lag behind the work is
worse than no backlog: it deceives."*

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

## Code review observations (`../review/`)

Above this repo, at `../review/` (i.e. `/Users/you/ai/code_pro/review/`),
three different AI CLIs — **Claude Code, Gemini, and Codex** — periodically
produce parallel-perspective code reviews of pxx. Each reviewer tends to excel
at slightly different roles, so the multi-reviewer setup is intentional.

**Layout (since 2026-05-10):** agent-namespaced. Each agent's files live at
`../review/<agent>/<agent>-*.md` — both the folder and the filename prefix
must match the agent name. `../review/inventory.md` is the authoritative
rule statement; `../review/README.md` is the landing index.

**Claude Code's writable surface is `../review/claude/` only.** This agent
may create and refresh files under `../review/claude/` whose names match
`claude-*.md`. Everything else under `../review/` (Codex's folder, Gemini's
folder, the two shared root files except for Claude's own section in
`inventory.md`) is **read-only** for Claude. Do not edit files outside the
Claude namespace, even to fix obvious typos in another agent's work — surface
the observation in `claude-followups.md` instead.

When the user cites a finding ("flagged in codex-04-observations.md..."):
1. Locate the file in `../review/` and read the cited item in full.
2. If the finding maps to a pxx change, address it by editing the relevant
   code/docs *in the pxx repo*.
3. If the finding maps to a Claude-perspective response, capture it in
   `../review/claude/claude-followups.md` (or refresh the relevant
   `claude-*.md` file).
4. Leave other agents' findings alone unless the user routes them — they may
   be intentional, queued, or under review by that agent.

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
| `PXX_STUDIO_LAN_URL`   | Override `http://workstation:11434` |
| `PXX_STUDIO_REMOTE_URL`| Studio's VPN-reachable URL (empty by default) |
| `PXX_MODEL`            | Force a specific model regardless of endpoint |
