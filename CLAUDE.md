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

```bash
# Install (editable) — uses uv tool
uv tool install --editable . --python 3.12

# Pre-flight: which endpoints are up, memory pressure, loaded models, CPU temp
bash scripts/doctor.sh

# Run pxx in another project directory
cd ~/some-python-project && pxx
```

There is **no test suite** in this repo and `pyproject.toml` declares no test
dependencies. The `/test` slash command and the `test-cmd: "pytest -q"` in
`config/aider.conf.yml` are for *downstream* projects that pxx is used to edit,
not for pxx itself. Don't claim a change is "verified" without saying how.

After editing `cli.py` or `endpoints.py`, the running pxx/aider session still
has old code in memory — the user must exit and re-launch to test.

## Architecture (the parts that span multiple files)

**`pxx/cli.py`** is the only entry point (`pxx.cli:main`). It:
1. Calls `detect_endpoint()` from `pxx/endpoints.py`
2. Sets `OLLAMA_API_BASE` from the chosen endpoint
3. Picks model via `model_for(endpoint)` — only `name == "neo"` gets `NEO_MODEL`,
   everything else (including the explicit `override` endpoint) gets `STUDIO_MODEL`.
   If overriding to a small box, the user must also set `PXX_MODEL`.
4. Locates the aider binary (prefers same-venv) and `os.execv`s into it, passing
   `--read pxx/prompts/system.md`, `--config config/aider.conf.yml`,
   `--model-settings-file config/model-settings.yml`, plus `--no-git` when cwd
   is not a git repo.

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

## Code review observations (`review/*.md`)

The user maintains a `review/` directory with periodic code-review output (e.g.
`review/04-observations.md`, `review/discrepancies.md`). Each file is a snapshot,
typically opening with *"Don't act unless asked."*

When the user cites a finding ("flagged in 04-observations.md..."):
1. Locate the file and read the cited item in full before acting
2. Address only what they pointed at unless they expand scope
3. Leave the other findings alone — they may be intentional, queued, or under
   review

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
