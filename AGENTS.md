# AGENTS.md — working agreements for AI agents in this repo

## What this is

pxx 2.0: a local-first, async AI coding-agent runtime. **DESIGN.md is the
authoritative architecture contract** — read it before changing anything. If a
contract must change, change DESIGN.md in the same commit.

## Commands

- Test: `uv run pytest` (or `.venv/bin/python -m pytest`) — must stay green.
  Tests require **no network, no Ollama, no aider, no git repo**.
- Lint: `uv run ruff check pxx tests` — must stay clean.
- Run locally: `.venv/bin/pxx doctor`

## Hard rules

1. **Gates raise; telemetry suppresses.** Safety/scope/hook/budget decisions
   raise from `pxx.errors` and propagate. Audit and memory writes are
   best-effort and must never crash a session.
2. **Paths are untrusted.** Canonicalize (`pxx.safety.canonicalize`,
   symlinks resolved) before any gate decision. Never derive the trust
   boundary from model output.
3. **Audit is metadata-only.** No prompt bodies, file contents, diffs, or
   secrets in events/audit. Tool-event previews stay truncated.
4. **Async core.** New long-running operations are `async def`; tests drive
   them with `asyncio.run()` in sync functions (no pytest-asyncio).
5. **Pure core, thin I/O edges.** Policy modules take data and return
   decisions; subprocess/network/FS I/O stays injectable. Every module ships
   with tests in `tests/test_<module>.py`.
6. Memory is **context, never policy**. Hooks are **deterministic** gates —
   never let model output override them.
7. Keep the tool surface small (~8 built-ins). Small local models degrade
   past ~10 tools.

## Conventions

- Python >= 3.11, `from __future__ import annotations` everywhere,
  dataclasses over dicts, StrEnum for enums, `logging.getLogger("pxx")`
  (no `print` outside `cli.py`/`doctor.py`).
- Core dependency budget: stdlib + `httpx`. New core deps need a strong
  reason; optional functionality goes in extras.
- Follow the existing module layout in DESIGN.md.
