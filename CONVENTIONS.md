# pxx — self-development rules

These rules apply when using pxx to edit pxx itself.

## Hard guardrails — DO NOT MODIFY these files

`.aiderignore` enforces this. If a request would require changing one of these, **refuse and ask me to do it by hand**:

- `config/model-settings.yml` — Ollama context windows; wrong values can OOM the Studio
- `config/aider.conf.yml` — aider defaults; subtle behavior changes
- `.aider.conf.yml` — project-level aider config
- `pyproject.toml` — package metadata and dependencies
- `.aiderignore`, `CONVENTIONS.md` — meta files

## Editable (normal development)

- `pxx/cli.py`, `pxx/endpoints.py` — core dispatch, edit with care
- `pxx/prompts/system.md` — global system prompt; change takes effect next session
- `pxx/commands/*.md` — slash command files
- `README.md` — docs

## Style

- Python 3.11+, type hints on every signature, no `Any` without reason
- Standard library first; new third-party deps need an explicit justification
- `ruff format` compatible
- Shell scripts: `#!/usr/bin/env bash`, `set -euo pipefail`
- No comments unless the *why* is non-obvious
- Docstrings on public functions when the WHY is non-trivial; no docstrings for simple internal helpers

## Testing changes

- After editing `cli.py` or `endpoints.py`, restart pxx (the running session has old code in memory)
- After editing `prompts/system.md`, change takes effect next session only
- Verify in a non-pxx project too — pxx must remain general-purpose
